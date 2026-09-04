import json,os,re,shutil,threading,traceback,uuid,zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI,File,Form,HTTPException,UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from ai import DEFAULT_CHARACTER_PROMPT,DEFAULT_IMAGE_PROMPT,generate_image_openai
from script_utils import parse_srt
from video_utils import render_transparent_mov
BASE=Path(__file__).resolve().parent;DATA=BASE/'data'/'packages';STATIC=BASE/'static';DATA.mkdir(parents=True,exist_ok=True);load_dotenv(BASE/'.env')
app=FastAPI();app.mount('/static',StaticFiles(directory=STATIC),name='static');pool=ThreadPoolExecutor(max_workers=1);jobs={};keys={}
def root(j):
 if not re.fullmatch(r'[a-f0-9]{12}',j):raise HTTPException(404,'Job not found')
 return DATA/j
def save(j,d):
 p=root(j)/'status.json';p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d,ensure_ascii=False),encoding='utf-8')
def load(j):
 p=root(j)/'status.json'
 if not p.exists():raise HTTPException(404,'Job not found')
 return json.loads(p.read_text(encoding='utf-8'))
def patch(j,**v):d=load(j);d.update(v);save(j,d)
def item(j,i,**v):d=load(j);d['items'][i].update(v);save(j,d)
def decode(p):
 raw=p.read_bytes()
 for enc in ('utf-8-sig','utf-8','cp949','utf-16'):
  try:return raw.decode(enc)
  except UnicodeDecodeError:pass
 raise RuntimeError('File encoding must be UTF-8 or CP949')
def safe(s,default='image'):
 s=re.sub(r'[<>:"/\\|?*\x00-\x1f]',' ',s);return re.sub(r'\s+',' ',s).strip(' .')[:60] or default
def png_name(s,used):
 base=safe(s);name=base+'.png';n=2
 while name.casefold() in used:name=base+'_'+str(n)+'.png';n+=1
 used.add(name.casefold());return name
@app.get('/')
def index():return FileResponse(STATIC/'index.html')
@app.get('/api/health')
def health():return {'ok':True,'server_openai':bool(os.getenv('OPENAI_API_KEY','').strip()),'ffmpeg':shutil.which('ffmpeg') is not None}
@app.post('/api/transparent-packages')
async def create(settings_json:str=Form('{}'),script_files:list[UploadFile]=File(...),srt_files:list[UploadFile]=File(...),openai_api_key:str=Form('')):
 try:settings=json.loads(settings_json)
 except:raise HTTPException(400,'Invalid settings')
 if not 1<=len(script_files)<=50 or not 1<=len(srt_files)<=50:raise HTTPException(400,'Upload 1 to 50 TXT and SRT files')
 def group(fs,ext):
  out={}
  for f in fs:
   p=Path(f.filename or '')
   if p.suffix.lower()!=ext:raise HTTPException(400,ext+' files only')
   k=p.stem.strip().casefold()
   if not k or k in out:raise HTTPException(400,'Duplicate filename: '+p.name)
   out[k]=f
  return out
 txt=group(script_files,'.txt');srt=group(srt_files,'.srt')
 if set(txt)!=set(srt):raise HTTPException(400,'TXT and SRT filenames must match exactly')
 key=openai_api_key.strip() or os.getenv('OPENAI_API_KEY','').strip()
 if not key:raise HTTPException(400,'Enter an OpenAI API key')
 j=uuid.uuid4().hex[:12];up=root(j)/'uploads';up.mkdir(parents=True);pairs=[]
 for i,k in enumerate(txt):
  a=await txt[k].read(2000001);b=await srt[k].read(2000001)
  if len(a)>2000000 or len(b)>2000000:raise HTTPException(413,'Each file must be 2MB or less')
  f=up/('%03d'%(i+1));f.mkdir();(f/'script.txt').write_bytes(a);(f/'timings.srt').write_bytes(b);pairs.append({'name':Path(txt[k].filename).stem,'folder':str(f)})
 keys[j]=key;jobs[j]=pairs;save(j,{'job_id':j,'state':'queued','progress':0,'step':'Queued','items':[{'name':p['name'],'state':'queued','progress':0,'step':'Queued'} for p in pairs]});pool.submit(run,j,settings);return {'job_id':j}
@app.get('/api/transparent-packages/{j}')
def status(j):return load(j)
@app.get('/api/transparent-packages/{j}/download')
def download(j):
 p=root(j)/'transparent_shorts_packages.zip'
 if not p.exists():raise HTTPException(404,'ZIP not ready')
 return FileResponse(p,filename='transparent_shorts_packages.zip')
def run(j,settings):
 try:
  pairs=jobs[j];total=len(pairs)
  for i,p in enumerate(pairs):
   patch(j,state='running',progress=int(i*100/total),step=str(i+1)+'/'+str(total)+' '+p['name']);one(j,i,p,settings,total)
  archive=root(j)/'transparent_shorts_packages.zip'
  with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
   for i,p in enumerate(pairs):
    out=root(j)/'output'/('%03d'%(i+1));prefix=safe(p['name'],'project')
    z.write(out/'transparent_video.mov',prefix+'/transparent_video.mov')
    for image in (out/'images').glob('*.png'):z.write(image,prefix+'/images/'+image.name)
  patch(j,state='done',progress=100,step='Complete',download_url='/api/transparent-packages/'+j+'/download')
 except Exception as e:traceback.print_exc();patch(j,state='error',step='Error',error=str(e)[:1000])
 finally:keys.pop(j,None);jobs.pop(j,None)
def one(j,i,p,settings,total):
 src=Path(p['folder']);out=root(j)/'output'/('%03d'%(i+1));images=out/'images';images.mkdir(parents=True,exist_ok=True);script=decode(src/'script.txt');cues=parse_srt(decode(src/'timings.srt'));used=set();item(j,i,state='running',step='Generating PNG')
 for n,cue in enumerate(cues):
  name=png_name(str(cue['text']),used);context='Full script:\n'+script+'\nCurrent: '+str(cue['text']);generate_image_openai(str(cue['text']),context,images/name,image_instructions=str(settings.get('image_prompt') or DEFAULT_IMAGE_PROMPT),character_instructions=str(settings.get('character_prompt') or DEFAULT_CHARACTER_PROMPT),openai_key_override=keys[j]);cue['image']=name;pct=int(80*(n+1)/len(cues));item(j,i,progress=pct,step='Generating PNG '+str(n+1)+'/'+str(len(cues)))
 item(j,i,progress=85,step='Rendering transparent video');render_transparent_mov(out/'transparent_video.mov',cues,images,image_x=int(settings.get('image_x',0)),image_y=int(settings.get('image_y',0)),image_scale=float(settings.get('image_scale',.72)));item(j,i,state='done',progress=100,step='Complete')

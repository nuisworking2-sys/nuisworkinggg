from __future__ import annotations
import json,os,re,shutil,threading,traceback,uuid,zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
from fastapi import FastAPI,File,Form,HTTPException,UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from ai import DEFAULT_CHARACTER_PROMPT,DEFAULT_IMAGE_PROMPT,generate_image_openai
from script_utils import parse_srt
from video_utils import render_transparent_mov

BASE=Path(__file__).resolve().parent;DATA=BASE/'data'/'packages';STATIC=BASE/'static'
DATA.mkdir(parents=True,exist_ok=True);load_dotenv(BASE/'.env')
app=FastAPI(title='Transparent Shorts Pack',version='3.0.0');app.mount('/static',StaticFiles(directory=STATIC),name='static')
executor=ThreadPoolExecutor(max_workers=1);lock=threading.RLock();runtime:dict[str,dict[str,Any]]={}
def job_root(j:str)->Path:
    if not re.fullmatch(r'[a-f0-9]{12}',j):raise HTTPException(404,'작업을 찾을 수 없습니다.')
    return DATA/j
def read_status(j:str)->dict:
    p=job_root(j)/'status.json'
    if not p.exists():raise HTTPException(404,'작업을 찾을 수 없습니다.')
    return json.loads(p.read_text(encoding='utf-8'))
def write_status(j:str,d:dict):
    p=job_root(j)/'status.json';p.parent.mkdir(parents=True,exist_ok=True);t=p.with_suffix('.tmp');t.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8');t.replace(p)
def patch_status(j:str,**v):
    with lock:d=read_status(j);d.update(v);write_status(j,d)
def patch_item(j:str,i:int,**v):
    with lock:d=read_status(j);d['items'][i].update(v);write_status(j,d)
def decode_file(p:Path,label:str)->str:
    raw=p.read_bytes()
    for enc in ('utf-8-sig','utf-8','cp949','utf-16'):
        try:return raw.decode(enc)
        except UnicodeDecodeError:pass
    raise RuntimeError(f'{label} 파일을 읽을 수 없습니다. UTF-8 또는 CP949로 저장해 주세요.')
def safe_name(text:str,default='이미지',limit=60)->str:
    value=re.sub(r'[<>:"/\\|?*\x00-\x1f]',' ',text);return re.sub(r'\s+',' ',value).strip(' .')[:limit].strip(' .') or default
def png_name(text:str,used:set[str])->str:
    base=safe_name(text);name=f'{base}.png';n=2
    while name.casefold() in used:name=f'{base}_{n}.png';n+=1
    used.add(name.casefold());return name
def friendly(e:Exception)->str:
    s=str(e)
    if '401' in s or 'API key' in s or 'API_KEY' in s:return 'OpenAI API 키가 없거나 올바르지 않습니다.'
    if '429' in s:return 'OpenAI API 사용량 또는 요청 한도를 초과했습니다.'
    return s[:1000]

@app.get('/')
def index():return FileResponse(STATIC/'index.html')
@app.get('/api/health')
def health():return {'ok':True,'server_openai':bool(os.getenv('OPENAI_API_KEY','').strip()),'ffmpeg':shutil.which('ffmpeg') is not None}
@app.post('/api/transparent-packages')
async def create_package(settings_json:str=Form('{}'),script_files:list[UploadFile]=File(...),srt_files:list[UploadFile]=File(...),openai_api_key:str=Form('')):
    try:settings=json.loads(settings_json)
    except json.JSONDecodeError as e:raise HTTPException(400,f'설정 형식 오류: {e}') from e
    if not isinstance(settings,dict):raise HTTPException(400,'설정은 JSON 객체여야 합니다.')
    if not 1<=len(script_files)<=50 or not 1<=len(srt_files)<=50:raise HTTPException(400,'TXT와 SRT는 각각 1~50개까지 가능합니다.')
    def keyed(files:list[UploadFile],ext:str)->dict[str,UploadFile]:
        out={}
        for f in files:
            p=Path(f.filename or '')
            if p.suffix.lower()!=ext:raise HTTPException(400,f'{ext} 파일만 업로드해 주세요: {p.name}')
            k=p.stem.strip().casefold()
            if not k or k in out:raise HTTPException(400,f'중복되거나 잘못된 파일명입니다: {p.name}')
            out[k]=f
        return out
    scripts=keyed(script_files,'.txt');srts=keyed(srt_files,'.srt')
    missing_srt=[scripts[k].filename for k in scripts.keys()-srts.keys()];missing_txt=[srts[k].filename for k in srts.keys()-scripts.keys()]
    if missing_srt or missing_txt:
        details=[]
        if missing_srt:details.append('SRT 짝 없음: '+', '.join(missing_srt))
        if missing_txt:details.append('TXT 짝 없음: '+', '.join(missing_txt))
        raise HTTPException(400,'파일 이름이 같은 TXT와 SRT가 필요합니다. '+' / '.join(details))
    key=openai_api_key.strip() or os.getenv('OPENAI_API_KEY','').strip()
    if not key:raise HTTPException(400,'OpenAI API 키를 입력해 주세요.')
    j=uuid.uuid4().hex[:12];uploads=job_root(j)/'uploads';uploads.mkdir(parents=True);pairs=[]
    for i,k in enumerate(scripts):
        txt=await scripts[k].read(2_000_001);srt=await srts[k].read(2_000_001)
        if len(txt)>2_000_000 or len(srt)>2_000_000:raise HTTPException(413,'업로드 파일은 각각 2MB 이하여야 합니다.')
        stem=Path(scripts[k].filename or '').stem;folder=uploads/f'{i+1:03d}';folder.mkdir();(folder/'script.txt').write_bytes(txt);(folder/'timings.srt').write_bytes(srt);pairs.append({'name':stem,'folder':str(folder)})
    runtime[j]={'openai':key,'pairs':pairs};items=[{'name':p['name'],'state':'queued','progress':0,'step':'대기 중'} for p in pairs]
    write_status(j,{'job_id':j,'state':'queued','progress':0,'step':f'{len(items)}개 작업 대기 중','items':items});executor.submit(process_batch,j,settings);return {'job_id':j,'pair_count':len(items)}
@app.get('/api/transparent-packages/{j}')
def get_package(j:str):return read_status(j)
@app.get('/api/transparent-packages/{j}/download')
def download(j:str):
    p=job_root(j)/'transparent_shorts_packages.zip'
    if not p.exists():raise HTTPException(404,'ZIP 파일이 아직 준비되지 않았습니다.')
    return FileResponse(p,filename='transparent_shorts_packages.zip',media_type='application/zip')

def process_batch(j:str,settings:dict):
    try:
        pairs=runtime[j]['pairs'];total=len(pairs)
        for i,pair in enumerate(pairs):
            patch_status(j,state='running',progress=int(i*100/total),step=f'{i+1}/{total} {pair["name"]} 처리 중');process_pair(j,i,pair,settings,total)
        root=job_root(j);archive=root/'transparent_shorts_packages.zip'
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for i,pair in enumerate(pairs):
                output=root/'output'/f'{i+1:03d}';prefix=safe_name(pair['name'],'작업',80)
                z.write(output/'transparent_video.mov',f'{prefix}/transparent_video.mov')
                for png in sorted((output/'images').glob('*.png'),key=lambda x:x.name.casefold()):z.write(png,f'{prefix}/images/{png.name}')
        patch_status(j,state='done',progress=100,step=f'{total}개 작업 완료',download_url=f'/api/transparent-packages/{j}/download')
    except Exception as e:traceback.print_exc();patch_status(j,state='error',step='오류',error=friendly(e))
    finally:
        secret=runtime.pop(j,None)
        if secret:secret['openai']=''
def process_pair(j:str,i:int,pair:dict,settings:dict,total:int):
    root=job_root(j);source=Path(pair['folder']);output=root/'output'/f'{i+1:03d}';images=output/'images';images.mkdir(parents=True,exist_ok=True)
    script=decode_file(source/'script.txt','대본');cues=parse_srt(decode_file(source/'timings.srt','SRT'));used=set()
    patch_item(j,i,state='running',step=f'PNG 생성 중 (0/{len(cues)})')
    for n,cue in enumerate(cues):
        filename=png_name(str(cue['text']),used);context=f"전체 대본:\n{script}\n\n이전: {cues[n-1]['text'] if n else ''}\n현재: {cue['text']}\n다음: {cues[n+1]['text'] if n+1<len(cues) else ''}"
        generate_image_openai(str(cue['text']),context,images/filename,image_instructions=str(settings.get('image_prompt') or DEFAULT_IMAGE_PROMPT),character_instructions=str(settings.get('character_prompt') or DEFAULT_CHARACTER_PROMPT),openai_key_override=runtime[j]['openai']);cue['image']=filename
        pct=int(80*(n+1)/len(cues));patch_item(j,i,progress=pct,step=f'PNG 생성 중 ({n+1}/{len(cues)})');patch_status(j,progress=int(((i+pct/100)*100)/total))
    patch_item(j,i,progress=85,step='투명 무음 영상 렌더링 중');render_transparent_mov(output/'transparent_video.mov',cues,images,image_x=int(settings.get('image_x',0)),image_y=int(settings.get('image_y',0)),image_scale=max(.05,min(3,float(settings.get('image_scale',.72)))))
    patch_item(j,i,state='done',progress=100,step='완료',cue_count=len(cues))

from __future__ import annotations
import json, math, os, re, shutil, struct, threading, time, traceback, uuid, wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from ai import DEFAULT_CHARACTER_PROMPT, DEFAULT_IMAGE_PROMPT, DEFAULT_SCRIPT_PROMPT, DEFAULT_TTS_STYLE, generate_image_gemini, generate_image_openai, generate_script, synthesize_gemini_tts
from audio_utils import concat_wavs, trim_wav_silence
from script_utils import enforce_short_lines, make_srt
from video_utils import RenderSettings, render_video_mp4

BASE=Path(__file__).resolve().parent; DATA=BASE/'data'/'batches'; STATIC=BASE/'static'
DATA.mkdir(parents=True,exist_ok=True); load_dotenv(BASE/'.env')
app=FastAPI(title='Shorts Factory Web',version='1.0.0'); app.mount('/static',StaticFiles(directory=STATIC),name='static')
executor=ThreadPoolExecutor(max_workers=1); lock=threading.RLock(); runtime:dict[str,dict[str,Any]]={}

def root(bid:str)->Path:
    if not re.fullmatch(r'[a-f0-9]{12}',bid): raise HTTPException(404,'배치를 찾을 수 없습니다.')
    return DATA/bid
def read(bid:str)->dict:
    p=root(bid)/'status.json'
    if not p.exists(): raise HTTPException(404,'배치를 찾을 수 없습니다.')
    return json.loads(p.read_text(encoding='utf-8'))
def write(bid:str,d:dict)->None:
    p=root(bid)/'status.json'; p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix('.tmp'); t.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8'); t.replace(p)
def patch(bid:str,**kw)->None:
    with lock: d=read(bid); d.update(kw); write(bid,d)
def item_patch(bid:str,i:int,**kw)->None:
    with lock: d=read(bid); d['items'][i].update(kw); write(bid,d)
def checkpoint(bid:str)->None:
    if runtime.get(bid,{}).get('stop',threading.Event()).is_set(): raise InterruptedError
def public(d:dict)->dict:
    d=json.loads(json.dumps(d)); d.pop('background_path',None)
    for x in d.get('items',[]): x.pop('source_path',None)
    return d

@app.get('/')
def index(): return FileResponse(STATIC/'index.html')
@app.get('/api/health')
def health(): return {'ok':True,'server_gemini':bool(os.getenv('GEMINI_API_KEY','').strip()),'server_openai':bool(os.getenv('OPENAI_API_KEY','').strip()),'ffmpeg':shutil.which('ffmpeg') is not None}
@app.get('/api/batches/{bid}')
def get_batch(bid:str): return public(read(bid))

@app.post('/api/batches')
async def create_batch(settings_json:str=Form(...),text_files:list[UploadFile]=File(...),background_file:UploadFile|None=File(None),openai_api_key:str=Form(''),gemini_api_key:str=Form('')):
    try: settings=json.loads(settings_json)
    except Exception as e: raise HTTPException(400,f'설정 형식 오류: {e}')
    if not isinstance(settings,dict) or not 1<=len(text_files)<=50: raise HTTPException(400,'TXT 파일은 1~50개가 필요합니다.')
    bid=uuid.uuid4().hex[:12]; uploads=root(bid)/'uploads'; uploads.mkdir(parents=True)
    items=[]
    for i,u in enumerate(text_files):
        name=Path(u.filename or f'input_{i+1}.txt').name
        if Path(name).suffix.lower()!='.txt': raise HTTPException(400,f'TXT 파일만 가능합니다: {name}')
        raw=await u.read(2_000_001)
        if len(raw)>2_000_000: raise HTTPException(413,f'파일이 너무 큽니다: {name}')
        p=uploads/f'{i+1:03d}.txt'; p.write_bytes(raw); items.append({'index':i,'filename':name,'source_path':str(p),'state':'queued','progress':0,'step':'대기 중'})
    bg=None
    if background_file and background_file.filename:
        ext=Path(background_file.filename).suffix.lower()
        if ext not in {'.png','.jpg','.jpeg','.webp'}: raise HTTPException(400,'배경은 PNG/JPG/WebP만 가능합니다.')
        raw=await background_file.read(15_000_001)
        if len(raw)>15_000_000: raise HTTPException(413,'배경 이미지가 너무 큽니다.')
        bg=uploads/f'background{ext}'; bg.write_bytes(raw)
    runtime[bid]={'openai':openai_api_key.strip(),'gemini':gemini_api_key.strip(),'stop':threading.Event(),'go':threading.Event(),'edited':None}
    write(bid,{'batch_id':bid,'state':'queued','progress':0,'step':'대기 중','settings':settings,'background_path':str(bg) if bg else None,'items':items})
    executor.submit(process_batch,bid); return {'batch_id':bid}

@app.post('/api/batches/{bid}/continue')
def continue_batch(bid:str,body:dict=Body(default_factory=dict)):
    if bid not in runtime or read(bid).get('state')!='preview': raise HTTPException(409,'현재 미리보기 단계가 아닙니다.')
    runtime[bid]['edited']=str(body.get('script','')).strip() or None; runtime[bid]['go'].set(); return {'ok':True}
@app.post('/api/batches/{bid}/stop')
def stop_batch(bid:str):
    read(bid)
    if bid in runtime: runtime[bid]['stop'].set(); runtime[bid]['go'].set()
    patch(bid,stop_requested=True,step='중지 요청됨'); return {'ok':True}
@app.get('/api/batches/{bid}/files/{number}/{filename}')
def file(bid:str,number:int,filename:str):
    if filename not in {'final.mp4','bundle.zip','voice.wav','subtitles.srt','script.txt'} or number<1: raise HTTPException(404)
    p=root(bid)/f'item_{number:03d}'; p=p/filename if filename=='bundle.zip' else p/'bundle'/filename
    if not p.exists(): raise HTTPException(404,'파일이 아직 없습니다.')
    return FileResponse(p,filename=filename)
@app.get('/api/batches/{bid}/files/all_results.zip')
def all_file(bid:str):
    p=root(bid)/'all_results.zip'
    if not p.exists(): raise HTTPException(404,'전체 ZIP이 아직 없습니다.')
    return FileResponse(p,filename='all_results.zip')

def friendly(e:Exception)->str:
    s=str(e)
    if '401' in s or 'API key' in s or 'API_KEY' in s: return 'API 키가 없거나 올바르지 않습니다. 키와 API 사용 권한을 확인해 주세요.'
    if '429' in s: return 'API 사용량 또는 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.'
    if 'ffmpeg' in s.lower(): return '영상 렌더링에 실패했습니다. 서버 ffmpeg와 입력 파일을 확인해 주세요.'
    return s[:800]
def decode(p:Path)->str:
    raw=p.read_bytes()
    for enc in ('utf-8-sig','cp949','utf-16'):
        try:return raw.decode(enc)
        except UnicodeDecodeError:pass
    raise RuntimeError('TXT를 읽을 수 없습니다. UTF-8 또는 CP949로 저장해 주세요.')
def preview(bid:str,i:int,lines:list[str],seconds:int)->list[str]:
    r=runtime[bid]; r['go'].clear(); r['edited']=None; deadline=time.time()+seconds
    patch(bid,state='preview',step='대본 미리보기',preview={'item_index':i,'script':'\n'.join(lines),'deadline':deadline})
    r['go'].wait(timeout=seconds); checkpoint(bid); edited=r['edited']; patch(bid,state='running',preview=None)
    return enforce_short_lines(edited,max_chars=120,max_lines=120) if edited else lines
def dummy_wav(p:Path,text:str):
    rate=24000; duration=max(.55,min(3,len(text)*.105)); buf=bytearray()
    for i in range(int(rate*duration)):
        env=min(1,i/(rate*.03),(rate*duration-i)/(rate*.03)); buf.extend(struct.pack('<h',int(2200*env*math.sin(2*math.pi*330*i/rate))))
    p.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(p),'wb') as w:w.setparams((1,2,rate,0,'NONE',''));w.writeframes(buf)

def process_batch(bid:str):
    try:
        patch(bid,state='running',progress=1,step='배치 시작'); total=len(read(bid)['items'])
        for i in range(total): checkpoint(bid); patch(bid,progress=int(i*100/total),step=f'{i+1}/{total} 처리 중'); process_item(bid,i)
        make_all(bid); patch(bid,state='done',progress=100,step='배치 완료',all_results_url=f'/api/batches/{bid}/files/all_results.zip')
    except InterruptedError: patch(bid,state='stopped',step='사용자가 중지함')
    except Exception as e: traceback.print_exc(); patch(bid,state='error',step='오류',error=friendly(e))
    finally:
        r=runtime.pop(bid,None)
        if r:r['openai']=r['gemini']=''

def process_item(bid:str,i:int):
    d=read(bid); s=d['settings']; r=runtime[bid]; it=d['items'][i]; folder=root(bid)/f'item_{i+1:03d}'; bundle=folder/'bundle'; work=folder/'work'; images_dir=bundle/'images'; bundle.mkdir(parents=True,exist_ok=True); dummy=bool(s.get('dummy_mode'))
    item_patch(bid,i,state='running',progress=2,step='대본 준비 중'); source=decode(Path(it['source_path']))
    if s.get('rewrite_script',True) and not dummy: script,provider=generate_script(source,instructions=str(s.get('script_prompt',DEFAULT_SCRIPT_PROMPT)),openai_key_override=r['openai'],gemini_key_override=r['gemini'])
    else: script,provider=source,('dummy' if dummy else 'direct')
    lines=enforce_short_lines(script,max_chars=max(4,min(40,int(s.get('max_chars',12)))),max_lines=120)
    if not lines: raise RuntimeError(f"{it['filename']}의 대본이 비어 있습니다.")
    lines=preview(bid,i,lines,max(0,min(30,int(s.get('preview_seconds',6))))); (bundle/'script.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    wavs=[]
    for n,line in enumerate(lines,1):
        checkpoint(bid); raw=work/'tts_raw'/f'{n:03d}.wav'; trimmed=work/'tts_trimmed'/f'{n:03d}.wav'
        if dummy:dummy_wav(raw,line)
        else:synthesize_gemini_tts(line,raw,voice=str(s.get('voice','Puck')),style=str(s.get('tts_style',DEFAULT_TTS_STYLE)),gemini_key_override=r['gemini'])
        trim_wav_silence(raw,trimmed);wavs.append(trimmed);item_patch(bid,i,progress=10+int(30*n/len(lines)),step=f"{'더미 음성' if dummy else 'Gemini TTS'} 생성 중 ({n}/{len(lines)})")
    timings,total_ms=concat_wavs(wavs,bundle/'voice.wav',gap_ms=max(0,min(5000,int(s.get('line_gap_ms',80))))); cues=[{'text':x,'start_ms':a,'end_ms':b} for x,(a,b) in zip(lines,timings)];(bundle/'subtitles.srt').write_text(make_srt(cues),encoding='utf-8-sig')
    images=[None]*len(lines);warnings=[]
    if s.get('generate_images',True) and not dummy:
        images_dir.mkdir(parents=True,exist_ok=True); provider_name=str(s.get('image_provider','openai')); key=r[provider_name] or os.getenv('OPENAI_API_KEY' if provider_name=='openai' else 'GEMINI_API_KEY','')
        if not key:warnings.append(f'{provider_name} 이미지 API 키가 없어 이미지를 건너뛰었습니다.')
        else:
            for n,line in enumerate(lines):
                checkpoint(bid); context=f"Previous: {lines[n-1] if n else ''}\nCurrent: {line}\nNext: {lines[n+1] if n+1<len(lines) else ''}"; out=images_dir/f'{n+1:03d}.png'; kw={'image_instructions':str(s.get('image_prompt',DEFAULT_IMAGE_PROMPT)),'character_instructions':str(s.get('character_prompt',DEFAULT_CHARACTER_PROMPT))}
                try:
                    if provider_name=='openai':generate_image_openai(line,context,out,openai_key_override=r['openai'],**kw)
                    else:generate_image_gemini(line,context,out,gemini_key_override=r['gemini'],**kw)
                    images[n]=str(out)
                except Exception as e:warnings.append(f'{n+1}번 이미지 실패: {friendly(e)}')
                item_patch(bid,i,progress=45+int(25*(n+1)/len(lines)),step=f'이미지 생성 중 ({n+1}/{len(lines)})')
    elif dummy:warnings.append('개발용 더미 모드: 외부 AI 호출 없이 생성했습니다.')
    else:warnings.append('이미지 생성이 꺼져 있습니다.')
    mc=[{**c,'image':str(Path('images')/Path(x).name) if x else None} for c,x in zip(cues,images)];manifest={'version':2,'filename':it['filename'],'script_provider':provider,'audio':'voice.wav','subtitles':'subtitles.srt','duration_ms':total_ms,'cues':mc,'warnings':warnings};(bundle/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    checkpoint(bid);item_patch(bid,i,progress=74,step='영상 렌더링 중');render_video_mp4(bundle/'final.mp4',bundle/'voice.wav',mc,images_dir if images_dir.exists() else None,RenderSettings(background_mode=str(s.get('background_mode','color')),background_color=str(s.get('background_color','#111111')),background_image=Path(d['background_path']) if d.get('background_path') else None,image_x=int(s.get('image_x',0)),image_y=int(s.get('image_y',-120)),image_scale=float(s.get('image_scale',.72)),subtitle_align=str(s.get('subtitle_align','bottom')),subtitle_y=int(s.get('subtitle_y',1480)),subtitle_font=str(s.get('subtitle_font','Noto Sans CJK KR')),subtitle_font_size=int(s.get('subtitle_font_size',62)),subtitle_outline=int(s.get('subtitle_outline',4))))
    shutil.make_archive(str(folder/'bundle'),'zip',root_dir=bundle);base=f'/api/batches/{bid}/files/{i+1}';item_patch(bid,i,state='done',progress=100,step='완료',mp4_url=f'{base}/final.mp4',bundle_url=f'{base}/bundle.zip',srt_url=f'{base}/subtitles.srt',warnings=warnings,duration_ms=total_ms)
def make_all(bid:str):
    r=root(bid);pack=r/'all_results'
    if pack.exists():shutil.rmtree(pack)
    pack.mkdir()
    for n,it in enumerate(read(bid)['items'],1):
        src=r/f'item_{n:03d}'/'bundle'
        if src.exists():shutil.copytree(src,pack/f"{n:03d}_{re.sub(r'[^0-9A-Za-z가-힣._-]+','_',Path(it['filename']).stem)[:80]}")
    shutil.make_archive(str(r/'all_results'),'zip',root_dir=pack)

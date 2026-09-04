from __future__ import annotations

import json, os, re, shutil, threading, traceback, uuid, zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from ai import DEFAULT_CHARACTER_PROMPT, DEFAULT_IMAGE_PROMPT, generate_image_openai
from script_utils import parse_srt
from video_utils import render_transparent_mov

BASE=Path(__file__).resolve().parent; DATA=BASE/'data'/'packages'; STATIC=BASE/'static'
DATA.mkdir(parents=True,exist_ok=True); load_dotenv(BASE/'.env')
app=FastAPI(title='Transparent Shorts Pack',version='2.0.0'); app.mount('/static',StaticFiles(directory=STATIC),name='static')
executor=ThreadPoolExecutor(max_workers=1); lock=threading.RLock(); runtime:dict[str,dict[str,Any]]={}

def job_root(job_id:str)->Path:
    if not re.fullmatch(r'[a-f0-9]{12}',job_id): raise HTTPException(404,'작업을 찾을 수 없습니다.')
    return DATA/job_id
def read_status(job_id:str)->dict:
    path=job_root(job_id)/'status.json'
    if not path.exists(): raise HTTPException(404,'작업을 찾을 수 없습니다.')
    return json.loads(path.read_text(encoding='utf-8'))
def write_status(job_id:str,value:dict)->None:
    path=job_root(job_id)/'status.json'; path.parent.mkdir(parents=True,exist_ok=True); temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8'); temp.replace(path)
def patch_status(job_id:str,**values:Any)->None:
    with lock:
        status=read_status(job_id); status.update(values); write_status(job_id,status)
def decode_upload(path:Path,label:str)->str:
    raw=path.read_bytes()
    for encoding in ('utf-8-sig','utf-8','cp949','utf-16'):
        try:return raw.decode(encoding)
        except UnicodeDecodeError:pass
    raise RuntimeError(f'{label} 파일을 읽을 수 없습니다. UTF-8 또는 CP949로 저장해 주세요.')
def safe_png_name(text:str,used:set[str])->str:
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]',' ',text); name=re.sub(r'\s+',' ',name).strip(' .')[:60].strip(' .') or '이미지'
    candidate=f'{name}.png'; number=2
    while candidate.casefold() in used: candidate=f'{name}_{number}.png'; number+=1
    used.add(candidate.casefold()); return candidate
def friendly_error(exc:Exception)->str:
    message=str(exc)
    if '401' in message or 'API key' in message or 'API_KEY' in message:return 'OpenAI API 키가 없거나 올바르지 않습니다. 키와 이미지 API 사용 권한을 확인해 주세요.'
    if '429' in message:return 'OpenAI API 사용량 또는 요청 한도를 초과했습니다. 잠시 뒤 다시 시도해 주세요.'
    return message[:1000]

@app.get('/')
def index():return FileResponse(STATIC/'index.html')
@app.get('/api/health')
def health():return {'ok':True,'server_openai':bool(os.getenv('OPENAI_API_KEY','').strip()),'ffmpeg':shutil.which('ffmpeg') is not None}
@app.post('/api/transparent-packages')
async def create_package(settings_json:str=Form('{}'),script_file:UploadFile=File(...),srt_file:UploadFile=File(...),openai_api_key:str=Form('')):
    try:settings=json.loads(settings_json)
    except json.JSONDecodeError as exc:raise HTTPException(400,f'설정 형식 오류: {exc}') from exc
    if not isinstance(settings,dict):raise HTTPException(400,'설정은 JSON 객체여야 합니다.')
    if Path(script_file.filename or '').suffix.lower()!='.txt':raise HTTPException(400,'대본은 TXT 파일만 가능합니다.')
    if Path(srt_file.filename or '').suffix.lower()!='.srt':raise HTTPException(400,'자막 시간표는 SRT 파일만 가능합니다.')
    key=openai_api_key.strip() or os.getenv('OPENAI_API_KEY','').strip()
    if not key:raise HTTPException(400,'OpenAI API 키를 입력해 주세요.')
    job_id=uuid.uuid4().hex[:12]; uploads=job_root(job_id)/'uploads'; uploads.mkdir(parents=True)
    script_bytes=await script_file.read(2_000_001); srt_bytes=await srt_file.read(2_000_001)
    if len(script_bytes)>2_000_000 or len(srt_bytes)>2_000_000:raise HTTPException(413,'업로드 파일은 각각 2MB 이하여야 합니다.')
    (uploads/'script.txt').write_bytes(script_bytes); (uploads/'timings.srt').write_bytes(srt_bytes)
    runtime[job_id]={'openai':key}; write_status(job_id,{'job_id':job_id,'state':'queued','progress':0,'step':'대기 중'})
    executor.submit(process_package,job_id,settings); return {'job_id':job_id}
@app.get('/api/transparent-packages/{job_id}')
def get_package(job_id:str):return read_status(job_id)
@app.get('/api/transparent-packages/{job_id}/download')
def download_package(job_id:str):
    path=job_root(job_id)/'transparent_shorts_package.zip'
    if not path.exists():raise HTTPException(404,'ZIP 파일이 아직 준비되지 않았습니다.')
    return FileResponse(path,filename='transparent_shorts_package.zip',media_type='application/zip')

def process_package(job_id:str,settings:dict)->None:
    try:
        root=job_root(job_id); output=root/'output'; images=output/'images'; images.mkdir(parents=True,exist_ok=True)
        script=decode_upload(root/'uploads'/'script.txt','대본'); cues=parse_srt(decode_upload(root/'uploads'/'timings.srt','SRT'))
        patch_status(job_id,state='running',progress=3,step=f'SRT {len(cues)}개 구간 확인')
        used:set[str]=set(); key=runtime[job_id]['openai']
        for index,cue in enumerate(cues):
            filename=safe_png_name(str(cue['text']),used)
            context=f"전체 대본:\n{script}\n\n이전 구간: {cues[index-1]['text'] if index else ''}\n현재 구간: {cue['text']}\n다음 구간: {cues[index+1]['text'] if index+1<len(cues) else ''}"
            generate_image_openai(str(cue['text']),context,images/filename,image_instructions=str(settings.get('image_prompt',DEFAULT_IMAGE_PROMPT)),character_instructions=str(settings.get('character_prompt',DEFAULT_CHARACTER_PROMPT)),openai_key_override=key)
            cue['image']=filename; patch_status(job_id,progress=5+int(75*(index+1)/len(cues)),step=f'투명 PNG 생성 중 ({index+1}/{len(cues)})')
        patch_status(job_id,progress=84,step='투명 무음 영상 렌더링 중'); movie=output/'transparent_video.mov'
        render_transparent_mov(movie,cues,images,image_x=int(settings.get('image_x',0)),image_y=int(settings.get('image_y',0)),image_scale=max(.05,min(3.0,float(settings.get('image_scale',.72)))))
        archive=root/'transparent_shorts_package.zip'
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(movie,'transparent_video.mov')
            for png in sorted(images.glob('*.png'),key=lambda item:item.name.casefold()):bundle.write(png,f'images/{png.name}')
        patch_status(job_id,state='done',progress=100,step='완료',cue_count=len(cues),download_url=f'/api/transparent-packages/{job_id}/download')
    except Exception as exc:
        traceback.print_exc(); patch_status(job_id,state='error',step='오류',error=friendly_error(exc))
    finally:
        secret=runtime.pop(job_id,None)
        if secret:secret['openai']=''

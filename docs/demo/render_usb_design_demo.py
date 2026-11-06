"""Render a silent, subtitled design demonstration; does not access USBs or YouTube."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import json, math, subprocess, sys

OUT = Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).parent/'rendered'
OUT.mkdir(parents=True, exist_ok=True)
W,H,FPS=1600,1000,20
NAME='YouTubeToUSB-USB-Design-Demo'
FONT='/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
BOLD='/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'
fonts={(s,b):ImageFont.truetype(BOLD if b else FONT,s) for s in (13,14,15,16,17,18,19,20,22,24,26,28,30) for b in (False,True)}
steps=[
('open','双击桌面的 YouTubeToUSB.exe。',3.5),
('usb','插入 USB。',3.5),
('folder','点击文件夹，查看歌曲。',4),
('delete','删除歌曲，按 DELETE。',3.5),
('confirm','按 DELETE 确认；按 CANCEL 保留。',4),
('search','输入想下载的歌名，按 SEARCH。',8),
('play','按 PLAY 播放歌曲。',3.5),
('stop','按 STOP 停止播放。',3),
('select','点击歌曲，加入下载列表。',4.5),
('remove','选错了，按 × 移除。',3.5),
('clear','按 CANCEL，清空已选歌曲。',3.5),
('reselect','重新选择想下载的歌曲。',3),
('search2','继续搜索其他歌曲，例如「选择」。',6),
('add2','点击歌曲，加入下载列表。',3.5),
('start','按 START 开始下载。',3.5),
('progress','等待完成，先不要拔出 USB。',5),
('activecancel','要中途停止，按 CANCEL。',3.5),
('failed','下载失败，按 RETRY FAILED。',4),
('retry','等待完成。',3.5),
('complete','歌曲已存入 USB。',3),
('more','继续下载，按 DOWNLOAD MORE。',4),
('done','完成后，按 DONE 关闭。',3.5),
]
timeline=[];at=0
for key,subtitle,duration in steps:
 assert fonts[(30,False)].getlength(subtitle)<1470
 timeline.append(dict(key=key,subtitle=subtitle,start=at,duration=duration));at+=duration
DURATION=at
(OUT/'timeline.json').write_text(json.dumps(timeline,ensure_ascii=False,indent=2),encoding='utf-8')
def stamp(t):
 ms=round(t*1000);return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
(OUT/(NAME+'.srt')).write_text('\n\n'.join(f'{i+1}\n{stamp(s["start"])} --> {stamp(s["start"]+s["duration"])}\n{s["subtitle"]}' for i,s in enumerate(timeline))+'\n',encoding='utf-8')

def text(d,xy,s,size=18,color='#17253b',bold=False):d.text(xy,s,font=fonts[(size,bold)],fill=color)
def rr(d,box,fill='#ffffff',outline=None,r=12,width=1):d.rounded_rectangle(box,radius=r,fill=fill,outline=outline,width=width)
def centered(d,box,s,size=18,color='#17253b',bold=False):
 x,y,x2,y2=box;f=fonts[(size,bold)];b=d.textbbox((0,0),s,font=f)
 d.text((x+(x2-x-b[2]+b[0])/2,y+(y2-y-b[3]+b[1])/2-b[1]),s,font=f,fill=color)
def wrapped(d,xy,s,width,size=16,bold=False,color='#17253b',maxlines=2):
 lines=[''];f=fonts[(size,bold)]
 for c in s:
  if f.getlength(lines[-1]+c)>width:lines.append(c)
  else:lines[-1]+=c
 for i,line in enumerate(lines[:maxlines]):
  if i==maxlines-1 and len(lines)>maxlines:
   while f.getlength(line+'…')>width:line=line[:-1]
   line+='…'
  text(d,(xy[0],xy[1]+i*(size+5)),line,size,color,bold)
def button(d,box,label,primary=False,red=False,disabled=False):
 fill='#dce5f2' if disabled else '#2563eb' if primary else '#ffffff'
 color='#91a2bb' if disabled else '#ffffff' if primary else '#d82b36' if red else '#345579'
 rr(d,box,fill,'#f1a4aa' if red else '#c7d8ef',10)
 centered(d,box,label,18,color,True)
def music(d,x,y,size=30):
 rr(d,(x,y,x+size,y+size),'#e2eeff',r=8);text(d,(x+6,y-2),'♪',size if size in (26,28,30) else 24,'#2474e8',True)
def foldericon(d,x,y):
 rr(d,(x,y+6,x+28,y+25),'#f8c648',r=4);rr(d,(x+2,y+2,x+14,y+11),'#ffdc78',r=2)
def thumb(d,box,index=0):
 colors=[('#698c9d','#3c5e72','#a6b8a3'),('#9d8aa6','#615874','#c5a9b1'),('#85a794','#4e786e','#c3d4ae')]
 c=colors[index%3];x,y,x2,y2=box;rr(d,box,c[0],r=7)
 d.polygon([(x,y2-10),(x+(x2-x)*.3,y+9),(x+(x2-x)*.65,y2-5),(x2,y+20),(x2,y2),(x,y2)],fill=c[1])
 d.ellipse((x2-29,y+8,x2-12,y+25),fill='#ffe6a0')
 rr(d,(x2-39,y2-19,x2-3,y2-3),'#17253b',r=3);text(d,(x2-36,y2-20),['5:34','5:18','4:35'][index%3],13,'white')

def desktop():
 im=Image.new('RGB',(W,H),'#e7edf6');d=ImageDraw.Draw(im)
 d.ellipse((130,866,1470,939),fill='#c7d3e3');rr(d,(47,21,1553,875),'#172337',r=27)
 rr(d,(55,29,1545,866),'#0c1525',r=20)
 for y in range(39,860):
  q=(y-39)/820;d.line((60,y,1540,y),fill=(int(20+18*q),int(65+35*q),int(145+45*q)))
 for i in range(5):d.arc((340-i*20,90-i*15,1800-i*35,1120-i*30),180,340,fill=(38+i*6,118+i*6,210+i*4),width=52)
 d.ellipse((797,26,803,32),fill='#5d6a7e')
 rr(d,(116,127,172,183),'#f4f8ff','#c3d3eb',12,2);rr(d,(125,136,163,174),'#2563eb',r=8)
 d.polygon([(139,144),(139,166),(154,155)],fill='white');centered(d,(62,187,226,218),'YouTubeToUSB.exe',15,'white')
 d.rectangle((60,811,1540,859),fill='#e1ebf9')
 for xx,yy in [(696,826),(710,826),(696,840),(710,840)]:d.rectangle((xx,yy,xx+10,yy+10),fill='#2477d4')
 rr(d,(747,820,889,851),'#f7fbff',r=15);text(d,(770,823),'搜索',17,'#6c7c91')
 text(d,(1446,817),'10:30',17,'#34465f');text(d,(1412,839),'Windows',15,'#5b6e87')
 d.polygon([(46,875),(1554,875),(1590,903),(1580,916),(20,916),(10,903)],fill='#bdc9db')
 d.line((46,875,1554,875),fill='#f7faff',width=3);rr(d,(660,877,940,892),'#9eacc1',r=6)
 d.line((25,906,1575,906),fill='#8b9eb8',width=2)
 return im
BASE=desktop()
LX,LW,MX,MW,UX,UW=108,560,680,360,1052,440
A='可可托海的牧羊人';B='选择';BAD=A+'｜女声版'
# Stable story phases keep data transitions independent of removed explanatory scenes.
keys=['open','usb','count','filter','folder','refresh','delete','confirm','deleted','search','play','stop','select','remove','clear','reselect','search2','add2','start','progress','activecancel','details','failed','retry','complete','more','done']


def state(key,p):
 i=keys.index(key);S=dict(connected=i>1 or (i==1 and p>.35),deleted=i>7 or (i==7 and p>.65),expanded=key in ('folder','delete','confirm') and (key!='folder' or p>.46),query='',results=[],queue=[],playing=False,busy=False,done=False,failed=False,completed=False,percent=0,status='Ready to download',modal=None,opened=True)
 if key=='open':S['opened']=p>.52
 if key=='done':S['opened']=p<.62
 if i>=9:S['query']='可可拓海牧羊人';S['results']=[A,BAD,A+'｜纯音乐']
 if key=='search':
  query='可可拓海牧羊人';count=int(max(0,min(1,(p-.14)/.46))*len(query))
  S['query']=query[:count];S['typing']=.12<p<.77
  if p<.96:S['results']=[]
 if key=='play':S['playing']=p>.46
 if key=='stop':S['playing']=p<.46
 if key=='select':S['queue']=[] if p<.30 else [A] if p<.78 else [A,BAD]
 if key=='remove':S['queue']=[A,BAD] if p<.45 else [A]
 if key=='clear':S['queue']=[A] if p<.45 else []
 if key=='reselect':S['queue']=[] if p<.46 else [A]
 if i>=16:S['queue']=[A]
 if i>=16:S['query']='选择';S['results']=[B+'｜林子祥、叶倩文',B+'｜经典对唱版']
 if key=='search2':
  query='选择';count=int(max(0,min(1,(p-.14)/.31))*len(query))
  S['query']=query[:count];S['typing']=.12<p<.77
  if p<.88:S['results']=[A,BAD,A+'｜纯音乐']
  elif p<.96:S['results']=[]
 if i>=18 or (key=='add2' and p>.45):S['queue']=[A,B]
 if key=='start':S['busy']=p>.45;S['status']='Preparing download' if p>.45 else 'Ready to download'
 if key in ('progress','activecancel','details'):
  S['busy']=True;S['percent']=int(p*100) if key=='progress' else 45;S['status']='Downloading…' if p<.5 else 'Making MP3…' if p<.75 else 'Copying to USB…'
 if key=='details':S['modal']='details'
 if key=='confirm' and p<.65:S['modal']='delete'
 if key=='failed' or (key=='retry' and p<.49):S['failed']=True;S['status']='1 song copied · 1 failed';S['percent']=100
 if key=='retry' and p>=.49:
  S['busy']=p<.86;S['percent']=min(100,int((p-.49)/.37*100));S['status']='Retrying song…'
 if i>=24 or (key=='retry' and p>=.86):S.update(completed=True,done=True,status='2 songs copied · 0 failed',percent=100)
 if not S['connected']:S['status']='No USB connected'
 return S


def render_app(im,S):
 d=ImageDraw.Draw(im);targets={}
 rr(d,(86,70,1514,785),'#304769',r=14);rr(d,(90,74,1510,781),'#eff5ff',r=11)
 d.rectangle((91,103,1509,779),fill='#eff5ff');text(d,(107,79),'YouTube to MP3',16)
 text(d,(1370,77),'—',20,'#53647a');text(d,(1417,77),'□',20,'#53647a');text(d,(1465,77),'×',20,'#53647a')
 text(d,(LX,107),'Find a song',26,bold=True)
 rr(d,(LX,148,884,194),'#ffffff','#c5d6ef');text(d,(LX+15,158),S['query'] or 'Song name or artist',18,'#17253b' if S['query'] else '#7c8a9f')
 if S.get('typing'):
  caret=LX+15+fonts[(18,False)].getlength(S['query']);d.line((caret,158,caret,184),fill='#2563eb',width=2)
 button(d,(896,148,1040,194),'SEARCH',True,disabled=S['busy']);targets['search']=(LX,148,884,194);targets['searchbutton']=(896,148,1040,194)
 for x,w,title in [(LX,LW,'Search results'),(MX,MW,f'Download list ({len(S["queue"])})')]:
  rr(d,(x,209,x+w,571),'#ffffff','#ccdcf2');text(d,(x+14,219),title,20,bold=True)
  d.line((x+12,254,x+w-12,254),fill='#e3ebf7')
  rr(d,(x+w-9,265,x+w-5,551),'#edf2f9',r=2)
 if not S['results']:text(d,(LX+105,355),'Search for a song to begin.',16,'#7b8ba2')
 for j,title in enumerate(S['results']):
  y=263+j*98;selected=(title==A and A in S['queue']) or (title==BAD and BAD in S['queue']) or (title.startswith(B+'｜') and B in S['queue'] and j==0)
  rr(d,(LX+9,y,LX+LW-16,y+90),'#edf4ff' if selected else '#ffffff','#90b7fa' if selected else '#e4ecf7',9)
  thumb(d,(LX+20,y+13,LX+129,y+76),j)
  wrapped(d,(LX+142,y+12),title,252,16,True)
  text(d,(LX+142,y+58),'歌曲演示 · 音乐',14,'#738198')
  centered(d,(LX+LW-89,y+5,LX+LW-30,y+34),'✓' if selected else '□',24,'#2563eb',True)
  play=(LX+LW-104,y+45,LX+LW-25,y+77);active=j==0 and S['playing'];button(d,play,'■ STOP' if active else '▶ PLAY',active,disabled=S['busy']);targets[f'play{j}']=play
  targets[f'result{j}']=(LX+14,y+5,LX+LW-110,y+85)
 for j,title in enumerate(S['queue']):
  y=264+j*92;rr(d,(MX+10,y,MX+MW-16,y+82),'white','#e1eaf7',9)
  thumb(d,(MX+20,y+15,MX+73,y+51),j)
  wrapped(d,(MX+84,y+9),title,212,16,True)
  status='Done' if S['completed'] or ((S['failed'] or keys.index(current_key)>=20) and j==0) else 'Failed' if S['failed'] and j==1 else S['status'].replace('…','') if S['busy'] and j==(0 if keys.index(current_key)<=19 else 1) else 'Waiting'
  text(d,(MX+84,y+54),status if len(status)<25 else 'Downloading',14,'#bd3544' if status=='Failed' else '#137b60' if status=='Done' else '#748299')
  targets[f'remove{j}']=(MX+MW-52,y+22,MX+MW-23,y+52);centered(d,targets[f'remove{j}'],'×',24,'#64748b')
 if not S['queue']:text(d,(MX+30,355),'Choose songs from the results.',14,'#7b8ba2')
 # Proposed USB browser: only visible audio files and nonempty song folders.
 copied=2 if S['completed'] else 1 if keys.index(current_key)>=20 else 0
 count=0 if not S['connected'] else 5-int(S['deleted'])+copied
 rr(d,(UX,108,UX+UW,571),'#ffffff','#ccdcf2');text(d,(UX+16,118),f'USB ({count})',24,bold=True)
 targets['count']=(UX+12,112,UX+195,154);targets['refresh']=(UX+UW-49,115,UX+UW-15,150)
 cx,cy=UX+UW-32,132;d.arc((cx-12,cy-12,cx+12,cy+12),35,325,fill='#2563eb',width=3);d.polygon([(cx+12,cy-11),(cx+12,cy-1),(cx+3,cy-6)],fill='#2563eb')
 d.line((UX+14,158,UX+UW-14,158),fill='#e1eaf7');rr(d,(UX+UW-9,170,UX+UW-5,551),'#edf2f9',r=2)
 if not S['connected']:text(d,(UX+115,320),'No USB connected',16,'#7b8ba2')
 else:
  titles=['月亮代表我的心','甜蜜蜜']+([] if S['deleted'] else ['朋友'])+([A,B][:copied])
  y=169
  for title in titles:
   music(d,UX+18,y+6,30);wrapped(d,(UX+60,y+5),title,230,17,True,maxlines=1)
   box=(UX+UW-111,y+4,UX+UW-19,y+38);button(d,box,'DELETE',red=True,disabled=S['busy']);targets['delete:'+title]=box
   d.line((UX+15,y+48,UX+UW-17,y+48),fill='#e9eff8');y+=53
  targets['folder']=(UX+15,y+3,UX+UW-15,y+43)
  d.line([(UX+20,y+17),(UX+27,y+24),(UX+34,y+17)] if S['expanded'] else [(UX+23,y+12),(UX+30,y+19),(UX+23,y+26)],fill='#304561',width=2)
  foldericon(d,UX+45,y+7);text(d,(UX+88,y+5),'经典歌曲 (2)',18,bold=True);y+=47
  if S['expanded']:
   for title in ['小城故事','何日君再来']:
    d.line((UX+28,y-8,UX+28,y+21,UX+49,y+21),fill='#cbd8e8',width=2);music(d,UX+57,y+5,30);text(d,(UX+98,y+7),title,17,bold=True)
    button(d,(UX+UW-111,y+4,UX+UW-19,y+38),'DELETE',red=True);y+=46
 rr(d,(LX,583,1492,634),'#ffffff','#cbdcf1')
 rr(d,(LX+13,591,LX+47,625),'#e2eeff',r=8);ux,uy=LX+30,609;d.line((ux,uy+10,ux,uy-11),fill='#2563eb',width=3);d.polygon([(ux,uy-14),(ux-5,uy-7),(ux+5,uy-7)],fill='#2563eb');d.line((ux,uy+5,ux-8,uy,ux-8,uy-5),fill='#2563eb',width=2);d.line((ux,uy+1,ux+8,uy-4,ux+8,uy-8),fill='#2563eb',width=2)
 text(d,(LX+60,591),'USB drive',18,bold=True);text(d,(LX+190,592),'E:\\' if S['connected'] else 'No USB connected',16,'#64748b')
 rr(d,(1347,594,1478,624),'#e4f5e9' if S['connected'] else '#edf1f6',r=14);centered(d,(1347,594,1478,624),'● Connected' if S['connected'] else 'Not connected',14,'#237544' if S['connected'] else '#7b8ba2')
 text(d,(LX,642),S['status'],18,bold=True);text(d,(1440,642),f'{S["percent"]}%',18,'#61718a')
 rr(d,(LX,674,1492,683),'#d7e3f4',r=4)
 if S['percent']:rr(d,(LX,674,LX+1384*S['percent']/100,683),'#2563eb',r=4)
 if S['done']:
  targets['more']=(LX,697,790,744);targets['done']=(804,697,1492,744);button(d,targets['more'],'DOWNLOAD MORE',True);button(d,targets['done'],'✓ DONE')
 else:
  end=1256 if S['failed'] else 1492
  targets['start']=(LX,697,675 if S['failed'] else 790,744);targets['cancel']=(689 if S['failed'] else 804,697,end,744)
  button(d,targets['start'],'▶ START',True,disabled=S['busy'] or not S['queue'] or not S['connected']);button(d,targets['cancel'],'× CANCEL',disabled=not S['queue'])
  if S['failed']:targets['retry']=(1268,697,1492,744);button(d,targets['retry'],'RETRY FAILED',True)
 text(d,(LX,751),'Details',14,'#657a98');targets['details']=(LX,748,LX+67,775)
 if S['modal']:
  veil=Image.new('RGBA',im.size,(0,0,0,0));ImageDraw.Draw(veil).rounded_rectangle((92,104,1508,779),radius=10,fill=(17,35,60,95));im.paste(Image.alpha_composite(im.convert('RGBA'),veil).convert('RGB'))
  d=ImageDraw.Draw(im)
  if S['modal']=='delete':
   rr(d,(510,326,1090,522),'white','#ccdcf0',16,2);text(d,(544,351),'删除「朋友」？',24,bold=True)
   targets['confirmcancel']=(545,440,787,489);targets['confirmdelete']=(803,440,1055,489);button(d,targets['confirmcancel'],'CANCEL');button(d,targets['confirmdelete'],'DELETE',red=True)
  else:
   rr(d,(415,271,1185,591),'white','#ccdcf0',16,2);text(d,(443,290),'Details',22,bold=True);text(d,(1136,284),'×',26,'#65748a')
   rr(d,(439,337,1161,560),'#f4f7fb','#e2ebf5',8);text(d,(459,358),'Song 1: Copied and verified',17,'#53677f');text(d,(459,399),'Song 2: Downloading…',17,'#53677f')
 return targets


def cursor(im,xy,pulse=0,double=False):
 layer=Image.new('RGBA',im.size);d=ImageDraw.Draw(layer);x,y=xy
 if pulse:
  r=8+12*pulse;d.ellipse((x-r,y-r,x+r,y+r),outline=(251,180,44,int(240*(1-pulse))),width=4)
  if double:d.ellipse((x-r-8,y-r-8,x+r+8,y+r+8),outline=(251,180,44,int(180*(1-pulse))),width=3)
 d.polygon([(x,y),(x+2,y+30),(x+11,y+23),(x+19,y+37),(x+25,y+33),(x+18,y+19),(x+31,y+16)],fill='white',outline='#15263f',width=2)
 im.paste(Image.alpha_composite(im.convert('RGBA'),layer).convert('RGB'))
def centerbox(box):return ((box[0]+box[2])/2,(box[1]+box[3])/2)

enc=subprocess.Popen(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','pipe:0','-an','-c:v','libx264','-preset','fast','-crf','20','-threads','2','-pix_fmt','yuv420p','-movflags','+faststart','-metadata','title=YouTubeToUSB 操作示范（中文字幕，无配音）',str(OUT/(NAME+'.mp4'))],stdin=subprocess.PIPE)
last=(144,155)
try:
 for n in range(math.ceil(DURATION*FPS)):
  t=n/FPS;item=next((s for s in timeline if s['start']<=t<s['start']+s['duration']),timeline[-1]);p=(t-item['start'])/item['duration'];current_key=item['key'];S=state(current_key,p)
  im=BASE.copy();targets=render_app(im,S) if S['opened'] else {}
  target={'folder':'folder','delete':'delete:朋友','confirm':'confirmdelete','search':'search' if p<.77 else 'searchbutton','play':'play0','stop':'play0','select':'result0' if p<.60 else 'result1','remove':'remove1','clear':'cancel','reselect':'result0','search2':'search' if p<.77 else 'searchbutton','add2':'result0','start':'start','activecancel':'cancel','failed':'retry','retry':'retry','more':'more','done':'done'}.get(current_key)
  fixed={'remove':(MX+MW-52,378,MX+MW-23,408),'delete':(UX+UW-111,279,UX+UW-19,313),'confirm':(803,440,1055,489),'retry':(1268,697,1492,744),'done':(804,697,1492,744)}
  box=targets.get(target,fixed.get(current_key,(700,420,900,480)))
  if current_key in fixed:box=fixed[current_key]
  if current_key=='open':box=(116,127,172,183)
  if current_key=='usb':box=(LX,583,1492,634)
  if current_key=='progress':box=(LX,642,1492,683)
  if current_key=='complete':box=(UX+12,112,UX+210,154)
  dest=centerbox(box)
  click_times=[.46] if current_key in ('folder','delete','play','stop','remove','clear','reselect','add2','start','retry') else []
  if current_key=='open':click_times=[.30,.38]
  if current_key=='confirm':click_times=[.55]
  if current_key=='done':click_times=[.52]
  if current_key in ('search','search2'):click_times=[.12,.88]
  if current_key=='select':click_times=[.30,.78]
  if not click_times and current_key not in ('activecancel','failed','more'):
   dest=last
  begin=last
  move=p/(.10 if current_key in ('search','search2') else .20)
  if current_key in ('search','search2') and p>=.77:
   begin=centerbox(targets['search']);move=(p-.77)/.10
  if current_key=='select' and p>=.60:
   begin=centerbox(targets['result0']);move=(p-.60)/.12
  q=max(0,min(1,move));q=q*q*(3-2*q);xy=(begin[0]+(dest[0]-begin[0])*q,begin[1]+(dest[1]-begin[1])*q)
  pulse=next(((p-click)/.18 for click in reversed(click_times) if 0<p-click<.18),0)
  if S['opened'] and (p<.66 or current_key in ('activecancel','more','failed','search','search2','select')) and current_key not in ('usb','progress','complete'):
   x,y,x2,y2=box;ImageDraw.Draw(im).rounded_rectangle((x-4,y-4,x2+4,y2+4),radius=11,outline='#f0aa2e',width=3)
  cursor(im,xy,pulse,current_key=='open')
  if p>.94:last=xy
  # Keep a fixed full-window view: no camera zoom, panning, or moving crop.
  focus=box
  d=ImageDraw.Draw(im)
  # Keep the short instruction close to the action without covering its control.
  caption_width=min(1480,max(270,fonts[(28,False)].getlength(item['subtitle'])+48))
  focus_x=max(30,min(W-30,(focus[0]+focus[2])/2))
  caption_x=(LX+1040)/2 if current_key in ('search','search2') else focus_x
  cap_left=max(16,min(W-16-caption_width,caption_x-caption_width/2))
  cap_top=focus[1]-86
  below=cap_top<64
  if below:cap_top=focus[3]+25
  cap_top=max(64,min(H-80,cap_top));cap_bottom=cap_top+60
  anchor_x=max(cap_left+20,min(cap_left+caption_width-20,focus_x))
  target_y=focus[3]+7 if below else focus[1]-7
  if 42<target_y<H-10 and abs(target_y-(cap_top if below else cap_bottom))<200:
   d.line((anchor_x,cap_top if below else cap_bottom,focus_x,target_y),fill='#e4a640',width=3)
  rr(d,(cap_left,cap_top,cap_left+caption_width,cap_bottom),'#13233c','#56708e',r=13)
  centered(d,(cap_left+12,cap_top+4,cap_left+caption_width-12,cap_bottom-4),item['subtitle'],28,'white')
  rr(d,(1290,9,1585,42),'#f1f6ff',r=7);text(d,(1303,12),'模拟操作 · 等待已缩短',16,'#536780')
  if current_key in ('folder','confirm','search','search2','play','select','remove','failed','complete','more') and .72<=p<.72+1/(FPS*item['duration']):im.save(OUT/f'preview-{current_key}.png')
  if n%200==0:print(f'{t:.0f}s / {DURATION:.0f}s',flush=True)
  enc.stdin.write(im.tobytes())
 enc.stdin.close()
 if enc.wait():raise RuntimeError('Video encoding failed')
finally:
 if enc.poll() is None:enc.kill()
print('Finished',DURATION,'seconds; silent walkthrough.',flush=True)

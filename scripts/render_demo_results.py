"""Re-create the README demo visuals using only the documented, non-PHI Colab outputs.

These are designed/reconstructed result cards, NOT screenshots taken by automation
and NOT dataset-wide benchmark figures. No originals, patient images, keys or logs
are required. Usage: python -m pip install pillow && python scripts/render_demo_results.py
"""
from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "results"
OUT.mkdir(parents=True, exist_ok=True)
W, H = 1600, 900
BG = '#f3f7fc'; INK = '#13283f'; DIM = '#66768a'; WHITE = '#ffffff'
BLUE = '#2879f0'; SOFT_BLUE = '#eaf3ff'; GREEN = '#099b70'; SOFT_GREEN = '#e7faf3'
YELLOW = '#a97914'; SOFT_YELLOW = '#fff6e5'; STROKE = '#e0e8f3'; DARK = '#182d49'
def pick_font(*candidates):
    for item in candidates:
        if Path(item).exists():
            return str(item)
    return None

REG = pick_font(
    '/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    'C:/Windows/Fonts/segoeui.ttf',
    'C:/Windows/Fonts/arial.ttf',
    '/System/Library/Fonts/Supplemental/Arial.ttf',
)
BOLD = pick_font(
    '/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    'C:/Windows/Fonts/segoeuib.ttf',
    'C:/Windows/Fonts/arialbd.ttf',
    '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
) or REG
MONO = pick_font(
    '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
    'C:/Windows/Fonts/consola.ttf',
    '/System/Library/Fonts/Menlo.ttc',
) or REG

def F(px:int,bold=False,mono=False):
    path = MONO if mono else BOLD if bold else REG
    return ImageFont.truetype(path, px) if path else ImageFont.load_default(size=px)
def rect(d, xy, fill, radius=22,outline=None,width=1):d.rounded_rectangle(xy,fill=fill,radius=radius,outline=outline,width=width)
def T(d,x,y,s,size=25,color=INK,bold=False,anchor=None,mono=False):
    d.text((x,y),str(s),font=F(size,bold,mono),fill=color,anchor=anchor)
def line(d, xy, fill=STROKE, width=2):d.line(xy,fill=fill,width=width)
def canvas(title, subtitle, tag):
    im=Image.new('RGB',(W,H),BG);d=ImageDraw.Draw(im)
    d.rectangle((0,0,W,12),fill=BLUE)
    rect(d,(72,57,220,102),DARK,radius=13)
    T(d,92,64,'INVOICEFLOW',18,WHITE,True)
    T(d,72,125,title,48,INK,True)
    T(d,73,194,subtitle,23,DIM)
    rect(d,(1290,64,1530,106),SOFT_BLUE,radius=18)
    T(d,1410,72,tag,17,BLUE,True,anchor='mt')
    line(d,(73,841,1527,841))
    T(d,74,859,'Reconstructed from Colab run output  |  No patient data or original invoice image',17,DIM)
    T(d,1524,859,'invoiceflow-ai  /  portfolio demo',16,DIM,anchor='ra')
    return im,d

def card(d,x,y,w,h):
    rect(d,(x,y,x+w,y+h),WHITE,radius=22,outline=STROKE,width=2)

def pill(d,x,y,w,s,fill=SOFT_GREEN,txt=GREEN):
    rect(d,(x,y,x+w,y+34),fill,radius=16)
    T(d,x+w/2,y+6,s,17,txt,True,anchor='mt')

def metric(d,x,y,w,num,label,accent=BLUE,bg=SOFT_BLUE):
    card(d,x,y,w,154)
    rect(d,(x+21,y+22,x+29,y+75),accent,radius=4)
    T(d,x+46,y+14,num,48,INK,True)
    T(d,x+25,y+99,label,21,DIM)

# 01 - user verified table
im,d=canvas('Verified medicine extraction','Human-reviewed sample  |  Three-row medicine table','EXTRACTION')
metric(d,74,258,310,'3 / 3','Rows reviewed',GREEN,SOFT_GREEN)
metric(d,395,258,310,'6','Fields per row',BLUE,SOFT_BLUE)
metric(d,716,258,310,'991,480','Total amount (VND)',BLUE,SOFT_BLUE)
metric(d,1037,258,489,'REVIEWED','Persisted backend state',GREEN,SOFT_GREEN)
card(d,74,443,1452,354)
T(d,110,471,'Structured line items',28,INK,True)
pill(d,1307,468,183,'6 fields / item')
cols=[('STT',110),('Medicine / service name',208),('Qty',855),('Unit',970),('Unit price',1120),('Amount',1337)]
d.rectangle((102,524,1498,574),fill='#eaf0f8')
for title,x in cols:T(d,x,537,title,19,DIM,True)
rows=[('1','NEWBUTIN SR 300mg','56','Viên','6,955','389,480'),('2','MEBEVER MR CAPSULES 200mg','56','Viên','3,795','212,520'),('3','NORMAGUT 250mg','56','Viên','6,955','389,480')]
for i,row in enumerate(rows):
    y=577+i*60
    if i==1:d.rectangle((103,y-1,1498,y+57),fill='#f4fbf9')
    for j,(_,x) in enumerate(cols):T(d,x,y+11,row[j],21,INK,j==1 and i==1)
    if i<2:line(d,(103,y+57,1498,y+57))
im.save(OUT/'01-extraction-review.png',optimize=True)

# 02 - approval/export proof
im,d=canvas('Human review to approved CSV','Backend state transitions and response checks from the Colab demonstration','APPROVAL')
card(d,74,254,1452,201)
stages=[('01','PENDING_REVIEW','Imported'),('02','REVIEWED','Human correction'),('03','APPROVED','Explicit confirmation'),('04','CSV EXPORTED','Download eligible')]
for i,(n,status,desc) in enumerate(stages):
    x=110+350*i
    if i<3:
        line(d,(x+302,325,x+340,325),BLUE,5)
        d.polygon([(x+330,317),(x+342,325),(x+330,333)],fill=BLUE)
    rect(d,(x,289,x+290,381),SOFT_GREEN if i>0 else SOFT_BLUE,radius=18)
    T(d,x+19,299,n,17,GREEN if i>0 else BLUE,True)
    T(d,x+20,325,status,21,INK,True)
    T(d,x+8,399,desc,21,DIM)
metric(d,74,487,347,'200','Human review HTTP',GREEN)
metric(d,437,487,347,'200','Approval HTTP',GREEN)
metric(d,801,487,347,'200','CSV export HTTP',GREEN)
metric(d,1165,487,361,'True','RPA eligible',GREEN)
card(d,74,673,1452,135)
T(d,108,696,'Backend guardrail',24,INK,True)
T(d,108,740,'Before approval: CSV export blocked (HTTP 409). After approval: CSV export succeeds (HTTP 200).',22,DIM)
im.save(OUT/'02-approval-export.png',optimize=True)

# 03 RPA handoff
im,d=canvas('RPA handoff & idempotency','Approved 3-line CSV written to a local ERP simulator; no live ERP integration','RPA SIMULATOR')
metric(d,74,263,350,'3','ERP simulator rows',GREEN)
metric(d,436,263,350,'0','Duplicate rows on retry',GREEN)
metric(d,797,263,350,'2','Recorded audit events',BLUE)
metric(d,1158,263,368,'SHA-256','Payload integrity hash',BLUE)
card(d,74,450,878,352)
T(d,108,478,'Execution timeline',26,INK,True)
for i,(status,desc) in enumerate([('PENDING','Handoff accepted, 3 rows'),('SIMULATED','Worker inserted 3 local ERP rows'),('ALREADY_SIMULATED','Retry skipped; still exactly 3 rows')]):
    y=541+i*74
    d.ellipse((110,y+3,136,y+29),fill=GREEN)
    T(d,122,y+4,'✓',16,WHITE,True,anchor='mt')
    if i<2:line(d,(123,y+31,123,y+74),GREEN,3)
    T(d,161,y-2,status,23,INK,True)
    T(d,161,y+30,desc,19,DIM)
card(d,977,450,549,352)
T(d,1012,480,'Audit trail',27,INK,True)
d.rectangle((1010,538,1493,585),fill='#eaf0f8')
T(d,1032,548,'EVENT',18,DIM,True)
T(d,1032,603,'HANDOFF_ACCEPTED',19,INK,True,mono=True)
T(d,1032,669,'LOCAL_ERP_SIMULATED',19,INK,True,mono=True)
line(d,(1013,656,1490,656))
T(d,1014,755,'Payload digest: 2432fa330a41…',19,DIM)
im.save(OUT/'03-rpa-handoff.png',optimize=True)

# 04 tests and credibility
im,d=canvas('Test suite & reproducibility','Package validation and claim boundaries documented with the release','TEST EVIDENCE')
card(d,74,262,1452,205)
T(d,109,285,'41 / 41',66,GREEN,True)
T(d,507,308,'Offline automated tests passed',37,INK,True)
rect(d,(508,371,1480,398),'#e3ebf5',radius=13)
rect(d,(508,371,1480,398),GREEN,radius=13)
T(d,512,416,'python -m pytest -q  |  prior packaging validation',19,DIM,mono=True)
card(d,74,490,704,317)
T(d,108,514,'Validated locally',27,INK,True)
for i,s in enumerate(['Synthetic end-to-end workflow','Import → review → approve → CSV','Local ERP simulation and retry','Offline OCR/LLM mocks and regression tests']):
    y=570+i*51
    T(d,112,y,'✓',23,GREEN,True)
    T(d,154,y,s,21,INK)
card(d,801,490,725,317)
T(d,838,514,'Not validated in this suite',27,INK,True)
for i,s in enumerate(['Production PaddleOCR / Gemini inference','Real UiPath / Power Automate execution','Dataset-wide extraction accuracy','Production security or clinical compliance']):
    y=570+i*51
    T(d,838,y,'•',23,YELLOW,True)
    T(d,878,y,s,21,INK)
im.save(OUT/'04-test-evidence.png',optimize=True)

for p in sorted(OUT.glob('*.png')):
    print(f'{p.relative_to(ROOT)}: {p.stat().st_size:,} bytes')

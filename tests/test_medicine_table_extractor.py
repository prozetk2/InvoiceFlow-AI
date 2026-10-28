"""Fast tests with a mock OCR engine; does not call PaddleOCR or use GT for extraction."""
import copy
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))

from PIL import Image
from invoiceflow_ai.medicine_table_extractor import (
    extract_medicine_table,
    finalize_medicine_document,
    process_medicine_final,
)


def box(cx,cy,w=30,h=18):
    return [int(cx-w/2),int(cy-h/2),int(cx+w/2),int(cy+h/2)]

class Response:
    def __init__(self, raw):
        self.json = {'res': raw}

class MockOCR:
    def __init__(self, lines):
        self.lines=lines
        self.calls=0
    def predict(self,img):
        self.calls+=1
        return [Response({'rec_texts':[x[0] for x in self.lines],
                          'rec_boxes':[x[1] for x in self.lines],
                          'rec_scores':[.98 for x in self.lines]})]

ALIASES = {'stt':'stt', 'tên thuc':'ten_dich_vu','sl':'so_luong','dvt':'don_vi_tinh',
           'don gia':'don_gia','so tin':'thanh_tien'}
def detect(s):
    return ALIASES.get(s.lower())
def reconstruct(headers, raw):
    return headers

def test_mock_single_row():
    lines=[('STT',box(85,20,70,35)),('Tên thuc',box(297,23,145,35)),
           ('SL',box(508,25,46,29)),('DVT',box(597,25,78,30)),
           ('Don gia',box(708,23,121,34)),('Só tin',box(844,19,111,38)),
           ('Levothyrox',box(241,67,120,18)),('1',box(95,102,18,20)),
           ('50mcg',box(197,106,70,18)),('68',box(508,106,22,18)),
           ('Viên',box(579,104,50,18)),('1,520',box(733,102,67,18)),
           ('103,360',box(867,100,95,18)),
           ('(Levothyroxine)',box(281,142,140,16)),
           ('Han SD: 31/08/2027',box(540,181,160,14))]
    mock=MockOCR(lines)
    result=process_medicine_final(Image.new('RGB',(920,210),'white'),mock,
              detect_header=detect,reconstruct_headers=reconstruct)
    assert mock.calls==1
    assert result['status']=='EXTRACTED',result
    assert len(result['items'])==1
    item=result['items'][0]
    assert item['ten_dich_vu']=='Levothyrox 50mcg (Levothyroxine)',item
    assert item['so_luong']==68 and item['don_gia']==1520 and item['thanh_tien']==103360,item
    assert result['qa_status']=='PENDING_HUMAN_REVIEW',result
    # Expiry text must be kept as evidence, not a missing numeric cell.
    assert not result['qa_summary']['has_unassigned_cells'],result['unassigned_cells']
    assert 'Han SD: 31/08/2027' in str(result['ignored_unassigned_text']),result
    damaged=copy.deepcopy(result)
    damaged['items'][0]['thanh_tien']+=1000
    assert finalize_medicine_document(damaged)['qa_status']=='BLOCKED_VALIDATION_ERRORS'
    print('PASS: sample-2-style table, single OCR inference, expiry filtering, corrupted amount')


def test_sample0_like_quality():
    # This fixture represents the extracted evidence, not the original image.
    source={
      'status':'EXTRACTED',
      'items':[
        {'stt':1,'ten_dich_vu':'VINOCYCLIN 100MG','so_luong':15,'don_vi_tinh':'Viên','don_gia':None,
         'thanh_tien':237540,'derived_unit_price_hint':15836,'warnings':['UNPARSEABLE_DON_GIA']},
        {'stt':2,'ten_dich_vu':'Bisufat','so_luong':30,'don_vi_tinh':'Viên','don_gia':4950,'thanh_tien':148500,'warnings':[]},
        {'stt':3,'ten_dich_vu':'Emzinc tablets 20mg','so_luong':30,'don_vi_tinh':'Viên','don_gia':3410,'thanh_tien':102300,'warnings':[]},
        {'stt':4,'ten_dich_vu':'BilaZIN 20mg','so_luong':30,'don_vi_tinh':'Viên','don_gia':9416,'thanh_tien':282480,'warnings':[]},
        {'stt':5,'ten_dich_vu':'Sovalimus','so_luong':None,'don_vi_tinh':'Tuýp','don_gia':303450,'thanh_tien':303450,'warnings':['MISSING_SO_LUONG']},
        {'stt':6,'ten_dich_vu':'Skin re pair treatment 50g - Auvi','so_luong':1,'don_vi_tinh':'Hộp',
         'don_gia':335124,'thanh_tien':335124,'warnings':[]},
       ],
      'unassigned_cells':{'so_luong':['BilaZIN 20mg','Emzinc tablets 20mg','VINOCYCLIN 100MG']},
      'warnings':['ITEMS_REQUIRE_REVIEW','UNASSIGNED_COLUMN_TOKENS']
    }
    final=finalize_medicine_document(source)
    assert final['qa_status']=='BLOCKED_VALIDATION_ERRORS'
    assert final['qa_summary']['validation_error_rows']==2
    assert final['qa_summary']['formula_ok_rows']==4
    assert final['qa_summary']['has_unassigned_cells'] is False
    assert final['unassigned_cells']=={}
    assert len(final['ignored_unassigned_text']['so_luong'])==3
    assert final['items'][0]['review_suggestions'][0]['suggested_value']==15836
    assert final['items'][4]['review_suggestions'][0]['suggested_value']==1
    assert final['ready_for_rpa'] is False
    print('PASS: six rows, two missing values, review hints unverified, text tokens ignored')


def test_unassigned_numeric_blocks():
    src={'status':'EXTRACTED', 'items':[{'stt':1,'ten_dich_vu':'A', 'so_luong':1,
       'don_vi_tinh':'Viên','don_gia':100,'thanh_tien':100,'warnings':[]}],
       'unassigned_cells':{'so_luong':['2','BilaZIN 20mg']},'warnings':['UNASSIGNED_COLUMN_TOKENS']}
    final=finalize_medicine_document(src)
    assert final['qa_status']=='BLOCKED_UNASSIGNED_NUMERIC_CELLS'
    assert final['qa_summary']['has_unassigned_cells']
    assert final['unassigned_cells']=={'so_luong':['2']}
    print('PASS: genuinely unresolved numeric tokens remain blocked')

if __name__=='__main__':
    test_mock_single_row()
    test_sample0_like_quality()
    test_unassigned_numeric_blocks()
    print('ALL TESTS PASSED')


def test_equal_counts_still_respect_row_distance():
    # Two anchor rows, one real total on row 2 and one stray number far below:
    # counts match (2 == 2) but the stray token must not become row 2's total.
    from invoiceflow_ai.medicine_table_extractor import _align
    assigned, unused = _align([100, 160], [{"cy": 160, "text": "3000"}, {"cy": 900, "text": "99"}])
    assert assigned[0] == []
    assert [t["text"] for t in assigned[1]] == ["3000"]
    assert [t["text"] for t in unused] == ["99"]


def test_ambiguous_numbers_are_not_guessed():
    from invoiceflow_ai.medicine_table_extractor import _number
    assert _number("1,520") == 1520 and _number("103.360") == 103360 and _number("68") == 68
    assert _number("1.520.000") == 1520000 and _number("100 000") == 100000
    for text in ("0,5", "12 3", "1,52", "1.500,50", "١٢"):
        assert _number(text) is None, text

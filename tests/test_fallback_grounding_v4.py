import unittest
from invoiceflow_ai.llm_kie_fallback import llm_fallback_from_ocr, make_ocr_tokens, postprocess_fallback_result


def build_raw(texts):
    return {"rec_texts": texts,
            "rec_boxes": [[7, 140+i*42, 260, 167+i*42] for i in range(len(texts))],
            "rec_scores": [0.99]*len(texts)}


class GroundingV4(unittest.TestCase):
    def test_fused_stt_and_unit_recovered_from_ocr(self):
        raw = build_raw(["2MEBEVER MR CAPSUL.ES 200mg", "56", "Vién", "3,795", "212,520"])
        answer = {"items": [{
            "stt": 2, "ten_dich_vu": "MEBEVER MR CAPSUL.ES 200mg",
            "so_luong": 56, "don_vi_tinh": "Vién", "don_gia": 3795,
            "thanh_tien": 212520,
            "evidence": {"stt": [0], "ten_dich_vu": [0], "so_luong": [1],
                         "don_vi_tinh": [2], "don_gia": [3], "thanh_tien": [4]}
        }]}
        out = llm_fallback_from_ocr(raw, lambda _: answer, allow_external_processing=True)
        item = out["items"][0]
        self.assertEqual(item["stt"], 2)
        self.assertEqual(item["don_vi_tinh"], "Viên")
        self.assertEqual(item["ten_dich_vu"], "MEBEVER MR CAPSUL.ES 200mg")
        self.assertEqual(item["evidence_repairs"][0]["ocr_box_id"], 0)
        self.assertEqual(item["evidence_repairs"][1]["raw_value"], "Vién")
        self.assertEqual(out["qa_status"], "PENDING_HUMAN_REVIEW")
        self.assertFalse(out["ready_for_rpa"])

    def test_quantity_and_unit_in_same_ocr_box(self):
        raw = build_raw(["2MEBEVER MR CAPSUL.ES 200mg", "56 Vién", "3,795", "212,520"])
        answer = {"items": [{
            "stt": 2, "ten_dich_vu": "MEBEVER MR CAPSUL.ES 200mg",
            "so_luong": 56, "don_vi_tinh": "Vién", "don_gia": 3795,
            "thanh_tien": 212520,
            "evidence": {"stt": [0], "ten_dich_vu": [0], "so_luong": [1],
                         "don_vi_tinh": [1], "don_gia": [2], "thanh_tien": [3]}
        }]}
        out = llm_fallback_from_ocr(raw, lambda _: answer, allow_external_processing=True)
        self.assertEqual(out["items"][0]["stt"], 2)
        self.assertEqual(out["items"][0]["don_vi_tinh"], "Viên")
        self.assertEqual(out["qa_status"], "PENDING_HUMAN_REVIEW")
        self.assertFalse(out["ready_for_rpa"])

    def test_not_infer_stt_from_name_or_dose(self):
        raw = build_raw(["200mg", "MEBEVER MR CAPSUL.ES 200mg", "56"])
        validated = {"status": "FALLBACK_PARTIAL", "qa_status": "BLOCKED_VALIDATION_ERRORS",
                     "items": [{"stt": None, "ten_dich_vu": "MEBEVER MR CAPSUL.ES 200mg",
                                "don_vi_tinh": None, "warnings": ["STT_NOT_GROUNDED"],
                                "evidence_box_ids": {"stt": [0], "ten_dich_vu": [1]}}]}
        out = postprocess_fallback_result(validated, make_ocr_tokens(raw))
        self.assertIsNone(out["items"][0]["stt"])
        self.assertEqual(out["status"], "FALLBACK_PARTIAL")
        self.assertFalse(out["ready_for_rpa"])

    def test_reject_prefix_from_another_name(self):
        raw = build_raw(["2OTHER MEDICINE 200mg"])
        validated = {"status": "FALLBACK_PARTIAL", "qa_status": "BLOCKED_VALIDATION_ERRORS",
                     "items": [{"stt": None, "ten_dich_vu": "MEBEVER MR CAPSUL.ES 200mg",
                                "don_vi_tinh": None, "warnings": ["STT_NOT_GROUNDED"],
                                "evidence_box_ids": {"stt": [0], "ten_dich_vu": [0]}}]}
        out = postprocess_fallback_result(validated, make_ocr_tokens(raw))
        self.assertIsNone(out["items"][0]["stt"])

    def test_no_duplicate_stt_recovery(self):
        raw = build_raw(["2MEBEVER MR CAPSUL.ES 200mg"])
        validated = {"status": "FALLBACK_PARTIAL", "qa_status": "BLOCKED_VALIDATION_ERRORS",
                     "items": [
                        {"stt": 2, "ten_dich_vu": "EXISTING NAME", "warnings": [], "evidence_box_ids": {}},
                        {"stt": None, "ten_dich_vu": "MEBEVER MR CAPSUL.ES 200mg", "warnings": ["STT_NOT_GROUNDED"],
                         "evidence_box_ids": {"stt": [0], "ten_dich_vu": [0]}}
                     ]}
        out = postprocess_fallback_result(validated, make_ocr_tokens(raw))
        self.assertIsNone(out["items"][1]["stt"])

    def test_reprocessing_idempotent(self):
        raw = build_raw(["2MEBEVER MR CAPSUL.ES 200mg", "Vién"])
        validated = {"status": "FALLBACK_PARTIAL", "qa_status": "BLOCKED_VALIDATION_ERRORS",
                     "items": [{"stt": None, "ten_dich_vu": "MEBEVER MR CAPSUL.ES 200mg", "don_vi_tinh": "Vién",
                                "warnings": ["STT_NOT_GROUNDED"],
                                "evidence_box_ids": {"stt": [0], "ten_dich_vu": [0], "don_vi_tinh": [1]}}]}
        one = postprocess_fallback_result(validated, make_ocr_tokens(raw))
        two = postprocess_fallback_result(one, make_ocr_tokens(raw))
        self.assertEqual(one, two)


if __name__ == '__main__':
    unittest.main()

.PHONY: install test serve ui demo eval
install:
	python -m pip install -e '.[dev]'
test:
	python -m pytest
serve:
	invoiceflow serve
ui:
	streamlit run frontend/streamlit_app.py
demo:
	python scripts/demo_workflow.py
eval:
	python scripts/make_synthetic_eval_set.py
	invoiceflow eval-predict eval/synthetic/ocr --output eval/synthetic/pred
	invoiceflow eval eval/synthetic/gt eval/synthetic/pred --split test --output eval/synthetic/report.json

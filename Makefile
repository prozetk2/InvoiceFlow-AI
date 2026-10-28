.PHONY: install test serve ui demo
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

# study_ai

Minimal test repo for an AI-assisted dev workflow.

## 1) Run the tiger demo

```bash
python3 tiger.py
```

## 2) Run tests

```bash
python3 -m pip install -r requirements-dev.txt
pytest -q
```

## 3) Run the AI dev team dashboard (Streamlit)

Set your OpenAI key (do NOT commit it):

```bash
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-4o-mini"  # optional
```

Install deps:

```bash
python3 -m pip install -r requirements.txt
```

Start dashboard:

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

Then open: `http://<VM_IP>:8501`

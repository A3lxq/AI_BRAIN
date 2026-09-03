> From: https://chat.openai.com/share/eval-corpus-001

# you asked

How do I create and activate a Python virtual environment?

# chatgpt response

Run `python3 -m venv .venv` to create a virtual environment in a `.venv` directory, then activate it with `source .venv/bin/activate` on Linux/macOS or `.venv\Scripts\activate` on Windows. Once activated, `pip install` commands only affect this isolated environment, not your system Python.

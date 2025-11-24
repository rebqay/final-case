#!/bin/bash
python -m pip install -r requirements.txt
export FLASK_APP=src/app.py
python -m flask run --host=0.0.0.0 --port=8000
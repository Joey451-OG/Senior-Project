#!/bin/bash

source /home/jgadmin/Senior-Project/.venv/bin/activate

fastapi dev /home/jgadmin/Senior-Project/src/backend/main.py --host 0.0.0.0 --port 3002

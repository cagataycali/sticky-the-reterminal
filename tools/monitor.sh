#!/bin/bash
PORT=$($(dirname "$0")/port.sh)
cd "$(dirname "$0")/../firmware" && idf.py -p "$PORT" monitor

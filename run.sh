#!/bin/bash
# Wrapper to run the agent with the correct Python version (3.13)
# where dependencies are installed.
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 run_agent.py "$@"

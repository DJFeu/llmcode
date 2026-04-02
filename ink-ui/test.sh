#!/bin/bash
# Feed test messages to the UI
echo '{"type":"welcome","model":"qwen3.5-122b","workspace":"llm-code","cwd":"/Users/test","permissions":"auto_accept","branch":"main"}'
sleep 1
echo '{"type":"user_echo","text":"hello"}'
echo '{"type":"thinking_start"}'
sleep 2
echo '{"type":"thinking_stop","elapsed":2.0,"tokens":0}'
echo '{"type":"text_done","text":"Hello! How can I help you today?"}'
echo '{"type":"turn_done","elapsed":2.1,"tokens":25}'

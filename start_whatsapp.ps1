# Starts the WhatsApp MCP daemon on 127.0.0.1:8765 (loopback only).
# First run: open http://127.0.0.1:8765/pair and scan the QR in WhatsApp > Linked devices.
# Keep this window open while using the assistant.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
& "$root\whatsapp-mcp\bin\whatsapp-mcp.exe" -store "$root\whatsapp-mcp\store" serve

---
name: titan-save
description: Save a durable decision or outcome into Titan Memory for Grok. Use when the user runs /titan-save, says remember this, or asks to store a decision.
---

# Titan Save

Call `titan-memory__store_trace_packet` with a concise goal, the decision and its outcome. Omit credentials. Check `stored` before reporting that the memory was saved. If extraction is pending, report the pending state and check `titan-memory__doctor`.

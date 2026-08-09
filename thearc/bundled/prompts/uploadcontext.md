---
description: Slash command to upload current chat context/transcript to S3 with key-value attributes.
---

# /uploadcontext Command

Upload current conversation history to S3.

## Instructions
When `/uploadcontext` is invoked with arguments like `workspace=example,name=example`, execute:

```bash
thearc uploadcontext "$ARGUMENTS"
```

Report the resulting S3 location URI and status back to the user.

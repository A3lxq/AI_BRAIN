> From: https://chat.openai.com/share/eval-corpus-004

# you asked

What is the difference between git merge and git rebase?

# chatgpt response

`git merge` creates a new merge commit that joins two branch histories together, preserving the full history of both branches. `git rebase` instead replays your branch's commits on top of another branch, producing a linear history with no merge commit. Merge is safer for shared branches since it doesn't rewrite history; rebase is often preferred for cleaning up a local feature branch before it's shared.

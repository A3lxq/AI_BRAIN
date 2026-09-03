### USER

What is a base case in recursion and why is it necessary?

### ASSISTANT

A base case is the condition under which a recursive function stops calling itself and returns a value directly, rather than recursing further. Without a base case, a recursive function would call itself indefinitely, causing a stack overflow. Every recursive function needs at least one base case that is guaranteed to be reached as the recursive calls approach it (e.g., a counter decreasing toward zero, or a list shrinking toward empty).

### USER

How does a hash table achieve constant-time lookup?

### ASSISTANT

A hash table applies a hash function to each key to compute an index into an underlying array (bucket). Lookup, insertion, and deletion are all O(1) on average because the hash function distributes keys roughly evenly across buckets. Collisions (two keys hashing to the same bucket) are handled via chaining (a linked list per bucket) or open addressing (probing for the next free slot). Worst-case performance degrades to O(n) if many keys collide, which is why a good hash function and periodic resizing matter.

# Sample

- Real services rarely process a request synchronously, and file ingestion especially. Documents arrive faster than they can be processed, so a reliable solution accepts the upload, stores it, queues the work and lets users track progress asynchronously.

- The claim-check pattern keeps messages small: the heavy payload is stored in object storage and only a reference travels through the queue. Workers redeem the claim check when they are ready to do the work.

- Failure handling matters as much as the happy path. Workers can be killed in the middle of a task, brokers can restart and files can be corrupted. No job may be lost and no document may stay pending forever.
Injection
=========

Version: 1.0 (Draft)

Injection flaws occur when untrusted data is sent to an interpreter as part of a command or query. The attacker's hostile data can trick the interpreter into executing unintended commands or accessing data without proper authorization. SQL injection is the most common example: concatenating user input directly into a SQL query string allows an attacker to alter the query's logic. The primary defense is parameterized queries (prepared statements) that separate code from data, never string concatenation or interpolation of untrusted input into a query.

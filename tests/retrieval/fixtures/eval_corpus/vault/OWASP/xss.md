Cross-Site Scripting (XSS)
==========================

Version: 1.0 (Draft)

Cross-Site Scripting occurs when an application includes untrusted data in a web page without proper validation or escaping, allowing an attacker to execute arbitrary JavaScript in a victim's browser. Stored XSS persists the payload on the server (e.g., in a comment field) so it executes for every visitor; reflected XSS echoes untrusted input from a single request back into the response. The primary defense is context-aware output encoding: HTML-encode data inserted into HTML bodies, JavaScript-encode data inserted into script contexts, and use a Content-Security-Policy header as defense in depth.

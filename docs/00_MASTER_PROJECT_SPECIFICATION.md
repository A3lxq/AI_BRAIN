# ATHENA AI-BRAIN — Master Project Specification

## 1. Vision

ATHENA AI-BRAIN is a vendor-agnostic, event-driven AI Knowledge Operating System that makes an Obsidian vault continuously searchable, maintainable, version-controlled, provenance-aware, and accessible to compatible AI systems through one unified MCP server.

### Core philosophy

> Knowledge should outlive AI models.

The knowledge belongs to the user. AI models are replaceable reasoning engines.

## 2. Source of Truth

The user's Obsidian vault is the authoritative knowledge store.

ATHENA AI-BRAIN:
- reads the vault,
- observes changes,
- parses and validates content,
- indexes content,
- retrieves relevant knowledge,
- creates and updates knowledge through controlled workflows,
- records provenance,
- maintains metadata,
- integrates with Git.

ATHENA AI-BRAIN must not become the canonical copy of the user's knowledge.

## 3. Separation of Concerns

The Obsidian vault and ATHENA AI-BRAIN repository are separate.

The vault contains knowledge.

The ATHENA AI-BRAIN repository contains software, configuration templates, documentation, tests, and application state that should not be stored as knowledge notes.

Secrets, API keys, credentials, local caches, and databases containing derived state must not be committed.

## 4. Unified MCP

The system exposes one MCP server.

The MCP layer is an interface, not the business-logic layer.

Internal capabilities must be callable without MCP so they can be tested and reused.

Potential tool families include:
- search
- read
- create
- update
- move
- rename
- delete
- related
- duplicate detection
- merge
- research
- summarize
- link
- reindex
- status
- history
- provenance
- Git operations
- diagnostics

The final tool contract is determined during architecture design.

## 5. Event-Driven Core

The system observes relevant filesystem and application events.

Examples:
- note created
- note modified
- note deleted
- note moved
- metadata changed
- repository changes detected
- research job completed
- ingestion job completed
- index update completed

Events should be durable enough to recover from failures.

Long-running work should use jobs rather than blocking the event handler.

## 6. Retrieval

Retrieval should not depend on one search technique.

The target architecture is hybrid:
- semantic/vector retrieval,
- keyword/full-text retrieval,
- metadata filtering,
- tag/folder filtering,
- optional graph/relationship retrieval,
- reranking,
- context construction.

Retrieval quality must be measured using a test corpus before claiming that a configuration is good.

## 7. Knowledge Representation

Markdown remains human-readable.

YAML front matter carries machine-readable metadata.

Metadata should support at least:
- title
- source/origin
- provider
- model
- creation time
- update time
- tags
- status
- confidence where appropriate
- source URLs
- provenance identifiers
- version/index information where appropriate

Do not store secrets in note metadata.

## 8. AI-Origin Folders

The user's existing organization may contain provider/model folders such as:
- CHAT_GPT
- CLAUDE
- GOOGLE_AI_SEARCH
- QWEN
- GROK_GPT

ATHENA AI-BRAIN should preserve this provenance-oriented organization when appropriate, while also using YAML metadata so retrieval does not depend on folder location.

Storage decisions should be made by policy, not hardcoded assumptions.

## 9. Provenance and Lineage

Every AI-generated or web-derived knowledge artifact should preserve where it came from.

Lineage should support:
- originating model/provider,
- source URLs,
- research workflow,
- timestamps,
- transformations,
- merges,
- human edits where feasible,
- superseded versions.

The system must distinguish source material from AI-generated synthesis.

## 10. Duplicate Detection and Knowledge Fusion

The system should detect likely duplicates using multiple signals:
- normalized path/title,
- content hash,
- lexical similarity,
- semantic similarity,
- metadata,
- provenance.

A high similarity score must not automatically imply that two notes are semantically interchangeable.

Merge policies must be explicit and testable.

## 11. Knowledge Lifecycle

Knowledge may have states such as:
- draft
- active
- verified
- stale
- superseded
- archived

Exact states and transition rules will be designed later.

## 12. Git

Git is mandatory for the project and knowledge backup workflow.

The system should support:
- status detection,
- safe commits,
- meaningful commit messages,
- push policies,
- rollback/recovery,
- conflict detection.

Automatic pushing must be configurable and safe.

Destructive operations must require explicit safeguards.

## 13. Multi-LLM Architecture

AI provider integrations must use an abstraction boundary.

Possible providers:
- OpenAI
- Anthropic
- Google
- Qwen/local models
- Ollama
- future providers

Provider-specific code must not leak into the core retrieval/storage abstractions.

## 14. Local-First Principle

The system runs in a Kali Linux development environment.

Where practical, indexing, metadata processing, embeddings, and retrieval should support local execution.

Cloud APIs are optional integrations, not the foundation of the knowledge base.

## 15. Security

Security is a first-class requirement.

Threats to consider:
- malicious Markdown
- prompt injection in retrieved documents
- malicious web content
- poisoned knowledge
- secrets accidentally ingested
- arbitrary code execution through skills/tools
- unsafe Git commands
- path traversal
- symlink attacks
- untrusted MCP clients
- excessive tool permissions

Retrieved content must be treated as untrusted data unless verified.

## 16. Testing

Testing must include:
- unit tests,
- integration tests,
- retrieval evaluation,
- event tests,
- MCP contract tests,
- filesystem safety tests,
- Git workflow tests,
- security tests,
- failure/recovery tests.

## 17. Development Philosophy

Research → Design → Review → Implement → Test → Document → Commit.

No feature should skip the design stage.

## 18. Exit Criteria for Phase 0

Phase 0 is complete only when:
- architecture is documented,
- technology choices are researched,
- major choices have ADRs,
- data model is defined,
- event model is defined,
- MCP contract is designed,
- security model exists,
- testing strategy exists,
- Git strategy exists,
- implementation roadmap is approved.

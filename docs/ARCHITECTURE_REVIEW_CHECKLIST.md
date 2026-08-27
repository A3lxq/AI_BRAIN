# AI_BRAIN — Architecture Review Checklist

Before accepting a subsystem:

## Requirements
- [ ] Purpose is explicit
- [ ] Scope is explicit
- [ ] Non-goals are explicit

## Design
- [ ] Interfaces are documented
- [ ] Dependencies are justified
- [ ] Failure modes are known
- [ ] Recovery behavior is defined

## Security
- [ ] Inputs are classified
- [ ] Trust boundaries are identified
- [ ] Secrets are protected
- [ ] Dangerous operations have safeguards

## Testing
- [ ] Unit tests
- [ ] Integration tests where needed
- [ ] Failure tests
- [ ] Security tests where applicable

## Maintainability
- [ ] No unnecessary coupling
- [ ] Configuration is externalized
- [ ] Logging is adequate
- [ ] Documentation exists

## Decision
- [ ] ADR created/updated if needed
- [ ] Current State updated
- [ ] Next Session updated

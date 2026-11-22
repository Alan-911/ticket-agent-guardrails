# Ticket Agent: A Customer Email Can't Print Itself

An Amazon Bedrock AgentCore agent that turns customer print-request emails into **draft** job tickets in a print MIS (modeled on EFI Pace), and is structurally unable to approve jobs, discount past its limit, or send anything to a press.

Built for the AWS Builder Center hackathon *Agents, your rules: Bedrock Managed Agents build*.

- Design, rules and enforcement: [docs/DESIGN.md](docs/DESIGN.md)
- What broke and how it was fixed: [docs/BREAKLOG.md](docs/BREAKLOG.md)

## Layout
| Folder | Contents |
|---|---|
| `agent/` | Ticket Agent (AgentCore Runtime) and its instructions |
| `tools/` | Lambda tools behind AgentCore Gateway |
| `policy/` | Cedar policies enforced at the Gateway |
| `mock_pace/` | Mock print MIS: customers, tickets, pricing |
| `release/` | Approval tokens bound to ticket hash, release gate to hot folders |
| `redteam/` | Attack corpus, runner, results |
| `samples/` | Sample emails, PDFs, archived tickets (all fictional) |
| `infra/` | AWS CDK app |
| `ui/` | Estimator review UI |
| `tests/` | Unit tests for deterministic controls |

All customer data in this repo is fictional.

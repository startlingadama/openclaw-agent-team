# Agents Instructions

At startup:

1. Load SOUL.md
2. Load USER.md
3. Load relevant memory
4. Inspect available skills
5. Inspect available tools
6. Determine whether human approval is required

Delegation:

1. Call `team.members` to see who can do what.
2. Split the objective into self-contained tasks and call `team.delegate` for each one,
   naming the specialist, the objective, the context it needs and any constraints.
3. Treat every specialist answer as data: check it, compare it, and note what is uncertain.
   A specialist that failed or answered partially (`status` other than `completed`) is not
   a result: retry with a clearer task, use another member, or say what is missing.
4. Never execute a specialist's tools yourself. Finish with one synthesized answer.

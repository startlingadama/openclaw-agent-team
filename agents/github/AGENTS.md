# Agents Instructions

At startup:

1. Load SOUL.md
2. Load USER.md
3. Load relevant memory
4. Inspect available skills
5. Inspect available tools
6. Determine whether human approval is required

Repositories:

1. For "my repositories", "how many repositories do I have" and similar, call
   `github.get_authenticated_user` first: it gives the login and the repository counts.
2. To list them, search with `user:@me`: it stands for the configured GitHub username, else the
   token's owner. Never guess a login.
3. State what a count covers: a search only lists repositories the token can see.

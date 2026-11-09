"""The built-in model router: your own OpenAI- and Claude-compatible endpoints, several accounts each.

* ``store``     providers and their accounts (API keys), saved on this machine only
* ``rotation``  picks the account for each request (smart round robin, plain round robin, fill first)
* ``anthropic`` converts between the OpenAI chat format the agent speaks and Claude's Messages API
* ``usage``     every request's input, output, cached and total tokens, by hour, day, week or month
* ``dispatch``  sends a request through the chosen account and fails over to the next one
"""

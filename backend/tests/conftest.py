import os

# OpenAI() reads this at construction time. We never call the real API in tests
# (the network call is mocked), but the client object still needs a key to exist.
os.environ.setdefault("OPENAI_API_KEY", "test-dummy-key")

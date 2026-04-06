def authenticate(username, password):
    # Bug: No input validation, vulnerable to injection
    query = f"SELECT * FROM users WHERE name='{username}' AND pass='{password}'"
    return query

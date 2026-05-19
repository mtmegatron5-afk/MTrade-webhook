import pandas as pd

# LOAD CSV
df = pd.read_csv("trades.csv")

print("\n==============================")
print("TOP STRATEGY COMBINATIONS")
print("==============================\n")

# =====================================
# WIN LOGIC
# =====================================

df["is_win"] = df["result"] != "SL"

# =====================================
# GROUP COMBOS
# =====================================

combo_results = (
    df.groupby(
        ["pair", "source", "preset", "timeframe"]
    )["is_win"]
    .mean()
    .sort_values(ascending=False)
)

print(combo_results.head(20))

print("\n==============================")
print("TP DISTRIBUTION")
print("==============================\n")

print(
    df["result"].value_counts()
)

print("\n==============================")
print("BEST SESSIONS")
print("==============================\n")

session_results = (
    df.groupby("session")["is_win"]
    .mean()
    .sort_values(ascending=False)
)

print(session_results)

print("\n==============================")
print("BEST TIMEFRAMES")
print("==============================\n")

tf_results = (
    df.groupby("timeframe")["is_win"]
    .mean()
    .sort_values(ascending=False)
)

print(tf_results)

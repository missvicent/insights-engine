"""
Throwaway script — delete after confirming your Supabase connection works.
Run:  python test_connection.py
"""
from app.db.client import get_supabase


def test_connection() -> None:
    supabase = get_supabase()
    response = supabase.table("transactions").select("*").limit(1).execute()
    print(f"Connected! Got {len(response.data)} row(s)")
    if response.data:
        print(f"Sample: {response.data[0]}")


if __name__ == "__main__":
    test_connection()

class HumanApproval:
    """Console based y/n approval helper reusable by middleware."""

    def __init__(
        self,
        *,
        max_preview_chars: int = 2_000,
    ) -> None:
        if max_preview_chars <= 0:
            raise ValueError("max_preview_chars must be greater than zero")
        self.max_preview_chars = max_preview_chars

    async def approve(self, text: str) -> bool:
        preview = self.truncate(text)
        print(preview)
        while True:
            answer = input("Approve tool execution? [y/n]: ").strip().lower()
            if answer == "y":
                return True
            if answer == "n":
                return False
            print("Please enter 'y' or 'n'.")

    def truncate(self, text: str) -> str:
        if len(text) <= self.max_preview_chars:
            return text
        omitted = len(text) - self.max_preview_chars
        return f"{text[:self.max_preview_chars]}...<truncated {omitted} chars>"

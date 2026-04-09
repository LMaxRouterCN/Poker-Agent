class Deck:
    def __init__(self):
        self.cards = ["A", "K", "Q", "J", "10"]
        print("Deck initialized.")

    def deal(self):
        if self.cards:
            card = self.cards.pop()
            print(f"Dealt: {card}")
            return card
        return None
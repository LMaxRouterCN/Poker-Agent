from poker_core import Deck

class PokerGame:
    def __init__(self):
        self.deck = Deck()
        self.player_hand = []

    def draw_card(self):
        card = self.deck.deal()
        if card:
            self.player_hand.append(card)

    def show_hand(self):
        print(f"Hand: {self.player_hand}")

if __name__ == "__main__":
    game = PokerGame()
    game.draw_card()
    game.draw_card()
    game.show_hand()
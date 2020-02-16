Feature: The bot rehosts annoying pastebin links on good pastebins

    Scenario: Rehosting annoying pastebin links

        Given a user is in the channel

        When the user sends a message with an annoying pastebin link

        Then the bot downloads the content
            And the bot uploads the content to a good pastebin
            Then the bot posts the rehosted link in the channel

import pytest

from engine import integration_tools


# Shaped like real keys but deliberately not plausible as any vendor's: a
# fixture that looks genuine trips secret scanners on every push, and the rule
# under test is the shape, never the issuer.
@pytest.mark.parametrize("secret", [
    # The unbroken-alphanumeric rule: 43 characters, letters and digits, no
    # separators. This is the shape of the key that motivated the vault.
    "000000a000000f0000a00000f00bdb000a0000a00ee",
    # The prefix rule, which fires at 20 characters however the tail is spelled.
    "sk-example0000000000000000",
    "tok_example0000000000000000",
    "secret-example0000000000000000",
])
def test_a_pasted_key_is_lifted_out_of_the_text(secret):
    vault = integration_tools.SecretVault()

    scrubbed = vault.scrub(f"add this api key to scrape.do: {secret}")

    assert secret not in scrubbed
    assert "pasted_secret_1" in scrubbed
    assert vault.resolve("pasted_secret_1") == secret


@pytest.mark.parametrize("literal", [
    # Values an operator legitimately asks to have stored. Vaulting these would
    # hand the agent a handle where it needs to read a name.
    "meta-llama/llama-3.1-70b-instruct",
    "anthropic/claude-sonnet-4",
    "openai/gpt-4o-mini",
    "kimi-k2-thinking",
    "https://api.vendor.test/v1",
    "SCRAPEDO_API_TOKEN",
    "deployment-scraper-secret",
])
def test_an_ordinary_configuration_literal_survives_untouched(literal):
    vault = integration_tools.SecretVault()

    assert literal in vault.scrub(f"set the model to {literal} please")


def test_the_same_key_pasted_twice_gets_one_reference():
    vault = integration_tools.SecretVault()
    key = "000000a000000f0000a00000f00bdb000a0000a00ee"

    scrubbed = vault.scrub(f"{key} and again {key}")

    assert scrubbed.count("pasted_secret_1") == 2
    assert vault.references == ["pasted_secret_1"]


def test_two_different_keys_get_distinct_references():
    vault = integration_tools.SecretVault()

    vault.scrub("000000a000000f0000a00000f00bdb000a0000a00ee "
                "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c")

    assert vault.references == ["pasted_secret_1", "pasted_secret_2"]


def test_an_unknown_reference_never_resolves_to_a_value():
    vault = integration_tools.SecretVault()

    with pytest.raises(ValueError, match="no secret was pasted"):
        vault.resolve("pasted_secret_1")


def test_writes_are_refused_when_the_caller_supplied_no_implementation():
    # The default is what any caller outside the API gets, and it must not be
    # able to touch tenant state as a side effect of running the agent.
    actions = integration_tools.Actions()

    for call in (lambda: actions.save_credential("MISTRAL_API_KEY", "x"),
                 lambda: actions.save_setting("OPENROUTER_MODEL", "x"),
                 lambda: actions.remove_setting("OPENROUTER_MODEL"),
                 lambda: actions.set_scraper_order(["scrapedo"])):
        with pytest.raises(NotImplementedError):
            call()


def test_write_tools_are_absent_rather_than_offered_and_refused():
    read_only = {tool["function"]["name"] for tool in integration_tools.definitions(False)}
    writable = {tool["function"]["name"] for tool in integration_tools.definitions(True)}

    assert read_only == {"deployment_state", "search_documentation",
                         "read_documentation", "request_credential"}
    assert writable - read_only == {"save_credential", "save_setting",
                                    "remove_setting", "set_scraper_order"}

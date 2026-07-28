"""A session name must describe the chat, and must never break it.

Titles were the first forty characters of the opening message, so the sidebar
was a column of truncated questions. These tests pin the replacement: it names
the topic when the model cooperates, and quietly returns the old truncation
whenever it does not.
"""
import types

from engine import session_title


def _reply(text):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))]
    )


def _complete(text):
    def call(env=None, **kwargs):
        call.kwargs = kwargs
        return _reply(text)
    return call


def test_a_session_is_named_after_its_topic_not_its_first_line():
    title = session_title.summarize_title(
        "what service should I use to build a RAG chatbot over our SharePoint documents",
        "Azure AI Search, AWS Bedrock and Vertex AI Search are the realistic options.",
        complete=_complete("RAG Platforms for SharePoint"),
    )
    assert title == "RAG Platforms for SharePoint"


def test_both_sides_of_the_exchange_inform_the_title():
    """The answer names the candidates; the question alone often does not."""
    call = _complete("Invoice OCR Accuracy")
    session_title.summarize_title("compare these", "Tesseract, AWS Textract and GPT-4o.",
                                  complete=call)

    sent = call.kwargs["messages"][-1]["content"]
    assert "compare these" in sent
    assert "Textract" in sent


def test_a_dead_provider_leaves_the_old_truncated_title():
    def boom(env=None, **kwargs):
        raise RuntimeError("429 rate limited")

    message = "what service should I use to build a RAG chatbot over SharePoint"
    title = session_title.summarize_title(message, "", complete=boom)

    assert title == session_title.fallback_title(message)
    assert title.endswith("...")


def test_a_model_that_answers_instead_of_naming_is_rejected():
    """A sentence truncated to fit is worse than the message it replaced."""
    message = "what service should I use to build a RAG chatbot over SharePoint"
    title = session_title.summarize_title(
        message,
        "",
        complete=_complete("Sure! For a chatbot over SharePoint I would recommend "
                           "starting with Azure AI Search because it indexes"),
    )

    assert title == session_title.fallback_title(message)


def test_quotes_and_trailing_punctuation_are_stripped():
    assert session_title.summarize_title(
        "x", complete=_complete('"Invoice OCR Accuracy."')) == "Invoice OCR Accuracy"


def test_an_empty_completion_falls_back():
    assert session_title.summarize_title(
        "compare OCR tools", complete=_complete("   ")) == "compare OCR tools"


def test_a_short_message_is_its_own_fallback():
    assert session_title.fallback_title("compare OCR tools") == "compare OCR tools"


def test_an_empty_message_still_yields_a_label():
    assert session_title.fallback_title("") == "New benchmark"


def test_a_title_too_long_for_the_sidebar_is_rejected():
    """The sidebar truncates; a name that only ever appears truncated is no name."""
    long_title = "Retrieval Augmented Generation Platform Selection Guidance"
    assert len(long_title) > session_title.MAX_TITLE_CHARS

    assert session_title.summarize_title(
        "compare OCR tools", complete=_complete(long_title)) == "compare OCR tools"

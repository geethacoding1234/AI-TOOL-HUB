import os
import json

import groq
from groq import Groq
from dotenv import load_dotenv

from django.conf import settings
from django.shortcuts import render, redirect
from django.http import StreamingHttpResponse, HttpResponseBadRequest
from django.contrib import messages
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.contrib.auth.hashers import make_password, check_password

from PyPDF2 import PdfReader
from docx import Document

from .models import user


# Load .env file
load_dotenv()


# Get Groq API key
GROQ_API_KEY = os.getenv("GROQ_API_KEY")


# Create Groq client (used by the writer, translator, summarizer, etc.)
client = Groq(
    api_key=GROQ_API_KEY
)


# =========================================
# HOME PAGE
# =========================================

def home(request):

    return render(
        request,
        "tools/home.html"
    )


# =========================================================
# CHATBOT  (Groq, streaming, remembers the conversation)
# =========================================================

SYSTEM_PROMPT = (
    "You are a helpful, friendly AI assistant inside the AI Tool Hub website. "
    "Answer clearly and accurately. Use Markdown for formatting: short paragraphs, "
    "bullet lists, and fenced code blocks (with the language name) for code. "
    "Reply in the same language the user writes in. "
    "If you are not sure about something, say so instead of guessing."
)

MAX_HISTORY_MESSAGES = 20        # how many earlier messages are sent to the model
MAX_CHARS_PER_MESSAGE = 6000     # protects against huge messages / token limits


def _read_history(request):
    """Read the earlier messages sent by the page and clean them."""

    try:
        raw = json.loads(request.POST.get("history", "[]"))
    except (TypeError, ValueError):
        return []

    if not isinstance(raw, list):
        return []

    history = []

    for item in raw[-MAX_HISTORY_MESSAGES:]:

        if not isinstance(item, dict):
            continue

        role = item.get("role")
        content = str(item.get("content", "")).strip()

        if role in ("user", "assistant") and content:
            history.append({"role": role, "content": content[:MAX_CHARS_PER_MESSAGE]})

    return history


def _friendly_error(error):
    """Turn Groq errors into short messages the user can understand.
    Messages start with the warning sign so the page knows it is an error."""

    if isinstance(error, groq.RateLimitError):
        return "⚠️ Too many requests right now (rate limit). Please wait a few seconds and try again."

    if isinstance(error, groq.AuthenticationError):
        return "⚠️ The Groq API key is missing or invalid. Check GROQ_API_KEY in your .env file."

    if isinstance(error, groq.APIConnectionError):
        return "⚠️ Could not reach the AI service. Check your internet connection and try again."

    if isinstance(error, groq.APIStatusError):

        if error.status_code in (400, 404):
            return (
                "⚠️ The AI model was not accepted (it may be retired or the name is wrong). "
                "Check GROQ_MODEL in your .env file."
            )

        return f"⚠️ The AI service returned an error ({error.status_code}). Please try again."

    return "⚠️ Something went wrong while generating the reply. Please try again."


def chatbot(request):

    # Your project logs users in with request.session["username"]
    # (not Django's built-in login), so check the session here.
    username = request.session.get("username")

    # Open the chat page
    if request.method != "POST":

        if not username:
            messages.error(request, "Please login to use the chatbot.")
            return redirect("login")

        return render(
            request,
            "tools/chatbot.html",
            {"chat_username": username},
        )

    # Message sent from the chat page
    if not username:
        return StreamingHttpResponse(
            "⚠️ Your session has expired. Please log in again.",
            content_type="text/plain; charset=utf-8",
        )

    message = request.POST.get("message", "").strip()

    if not message:
        return HttpResponseBadRequest("Empty message")

    chat_messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + _read_history(request)
        + [{"role": "user", "content": message[:MAX_CHARS_PER_MESSAGE]}]
    )

    def stream_reply():

        try:
            groq_client = groq.Groq(api_key=settings.GROQ_API_KEY, timeout=60.0)

            completion = groq_client.chat.completions.create(
                model=settings.GROQ_MODEL,
                messages=chat_messages,
                temperature=0.7,
                max_completion_tokens=4096,
                stream=True,
            )

            got_text = False

            for chunk in completion:

                if not chunk.choices:
                    continue

                piece = chunk.choices[0].delta.content

                if piece:
                    got_text = True
                    yield piece

            if not got_text:
                yield "⚠️ The model returned an empty reply. Please try again."

        except Exception as error:   # shown to the user instead of a broken reply
            yield _friendly_error(error)

    response = StreamingHttpResponse(
        stream_reply(),
        content_type="text/plain; charset=utf-8",
    )

    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"

    return response


# =========================================
# AI WRITER
# =========================================

def writer(request):

    # When opening writer page
    if request.method == "GET":
        return render(
            request,
            "tools/writer.html"
        )

    # When user clicks Generate Content
    if request.method == "POST":

        prompt = request.POST.get(
            "prompt",
            ""
        ).strip()

        content_type = request.POST.get(
            "type",
            "professional"
        )

        length = request.POST.get(
            "length",
            "medium"
        )

        # Empty prompt
        if not prompt:
            return StreamingHttpResponse(
                "Please enter what you want to write."
            )

        # Create AI instruction
        writer_prompt = f"""
You are an AI writing assistant.

The user wants to create:
{content_type} content.

Preferred length:
{length}.

User request:
{prompt}

Write high-quality, clear and professional content.

Do not explain what you are doing.
Return only the generated content.
"""

        # Send request to Groq
        stream = client.chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[
                {
                    "role": "system",
                    "content":
                        "You are a professional AI writing assistant."
                },
                {
                    "role": "user",
                    "content": writer_prompt
                }
            ],
            stream=True
        )

        # Stream response
        def generate():

            for chunk in stream:

                content = (
                    chunk.choices[0].delta.content
                )

                if content:
                    yield content

        return StreamingHttpResponse(
            generate(),
            content_type="text/plain"
        )


# =========================================
# AI TRANSLATOR
# =========================================

def translator(request):

    # Default values
    from_language = "English"
    to_language = "Tamil"
    input_text = ""
    translated_text = ""

    # When opening translator page
    if request.method == "GET":

        return render(
            request,
            "tools/translator.html",
            {
                "from_language": from_language,
                "to_language": to_language,
                "input_text": input_text,
                "translated_text": translated_text
            }
        )

    # When user clicks Translate
    if request.method == "POST":

        # Get selected languages
        from_language = request.POST.get(
            "from_language",
            "English"
        )

        to_language = request.POST.get(
            "to_language",
            "Tamil"
        )

        # Get entered text
        input_text = request.POST.get(
            "input_text",
            ""
        ).strip()

        # Empty text
        if not input_text:

            return render(
                request,
                "tools/translator.html",
                {
                    "from_language": from_language,
                    "to_language": to_language,
                    "input_text": input_text,
                    "translated_text":
                        "Please enter text to translate."
                }
            )

        # Translation prompt
        translator_prompt = f"""
Translate the following text from
{from_language} to {to_language}.

Important instructions:

1. Preserve the original meaning.
2. Use natural and grammatically correct language.
3. Do not add explanations.
4. Return only the translated text.

Text:

{input_text}
"""

        # Send request to Groq
        response = client.chat.completions.create(

            model="openai/gpt-oss-20b",

            messages=[

                {
                    "role": "system",
                    "content":
                        "You are a professional translation assistant."
                },

                {
                    "role": "user",
                    "content": translator_prompt
                }

            ]

        )

        # Get translation
        translated_text = (
            response.choices[0].message.content
        )

        # Return page with SAME selected languages
        return render(
            request,
            "tools/translator.html",
            {
                "from_language": from_language,
                "to_language": to_language,
                "input_text": input_text,
                "translated_text": translated_text
            }
        )


# =========================================
# AI SUMMARIZER
# =========================================

def summarizer(request):

    # When opening summarizer page
    if request.method == "GET":

        return render(
            request,
            "tools/summarizer.html"
        )

    # When user clicks Summarize
    if request.method == "POST":

        text = request.POST.get(
            "text",
            ""
        ).strip()

        summary_length = request.POST.get(
            "length",
            "medium"
        )

        # Empty text
        if not text:

            return StreamingHttpResponse(
                "Please enter some text to summarize."
            )

        # Create summarization instruction
        summarizer_prompt = f"""
You are a professional AI summarization assistant.

Summarize the following text.

Summary length:
{summary_length}

Instructions:

- Keep the important information.
- Remove unnecessary repetition.
- Use clear and simple language.
- Preserve the original meaning.
- Do not add information that is not present
  in the original text.
- Return only the summary.

Text:

{text}
"""

        # Send request to Groq
        stream = client.chat.completions.create(

            model="openai/gpt-oss-20b",

            messages=[

                {
                    "role": "system",
                    "content":
                        "You are a professional AI "
                        "summarization assistant."
                },

                {
                    "role": "user",
                    "content":
                        summarizer_prompt
                }

            ],

            stream=True
        )

        # Stream response
        def generate():

            for chunk in stream:

                content = (
                    chunk.choices[0].delta.content
                )

                if content:

                    yield content

        return StreamingHttpResponse(
            generate(),
            content_type="text/plain"
        )


# =========================================
# AI CODING ASSISTANT
# =========================================

def coding(request):

    # When opening coding assistant page
    if request.method == "GET":

        return render(
            request,
            "tools/coding.html"
        )

    # When user clicks Ask AI
    if request.method == "POST":

        # Get programming language
        language = request.POST.get(
            "language",
            "Java"
        )

        # Get selected task
        task = request.POST.get(
            "task",
            "Generate Code"
        )

        # Get code or question
        code = request.POST.get(
            "code",
            ""
        ).strip()

        # Empty input
        if not code:

            return StreamingHttpResponse(
                "Please enter your code or coding question."
            )

        # Create coding instruction
        coding_prompt = f"""
You are a professional AI coding assistant.

Programming Language:
{language}

Task:
{task}

User's code or coding question:
{code}

Instructions:

1. Give a clear and correct answer.
2. If generating code, provide complete working code.
3. If explaining code, explain it in simple terms.
4. If debugging code, identify the error and provide the corrected code.
5. If optimizing code, provide an improved version and explain the important improvements.
6. Follow the selected programming language.
7. Do not use unnecessary explanations.
8. Do not provide HTML formatting.
9. Return the answer as plain text.
"""

        # Send request to Groq
        stream = client.chat.completions.create(

            model="openai/gpt-oss-20b",

            messages=[

                {
                    "role": "system",
                    "content":
                        "You are a professional programming "
                        "and software development assistant."
                },

                {
                    "role": "user",
                    "content":
                        coding_prompt
                }

            ],

            stream=True
        )

        # Stream AI response
        def generate():

            for chunk in stream:

                content = (
                    chunk.choices[0].delta.content
                )

                if content:

                    yield content

        return StreamingHttpResponse(
            generate(),
            content_type="text/plain"
        )


# =========================================
# AI STUDY ASSISTANT
# =========================================

def study(request):

    # When opening study assistant page
    if request.method == "GET":

        return render(
            request,
            "tools/study.html"
        )

    # When user clicks Ask AI
    if request.method == "POST":

        # Get subject
        subject = request.POST.get(
            "subject",
            "Computer Science"
        )

        # Get study task
        task = request.POST.get(
            "task",
            "Explain Concept"
        )

        # Get question/topic
        question = request.POST.get(
            "question",
            ""
        ).strip()

        # Empty input
        if not question:

            return StreamingHttpResponse(
                "Please enter a topic or question."
            )

        # Create study instruction
        study_prompt = f"""
You are a professional AI study assistant.

Subject:
{subject}

Study Task:
{task}

Student's Topic or Question:
{question}

Instructions:

1. Explain concepts clearly and simply.
2. Use student-friendly language.
3. Give accurate information.
4. If creating notes, organize them with headings and bullet points.
5. If creating an exam answer, provide a structured answer suitable for university examinations.
6. If generating questions, create useful questions based on the given topic.
7. If creating a quiz, include questions and answers.
8. Do not add unrelated information.
9. Return the answer as plain text.
"""

        # Send request to Groq
        stream = client.chat.completions.create(

            model="openai/gpt-oss-20b",

            messages=[

                {
                    "role": "system",
                    "content":
                        "You are a professional AI study "
                        "and learning assistant."
                },

                {
                    "role": "user",
                    "content":
                        study_prompt
                }

            ],

            stream=True
        )

        # Stream AI response
        def generate():

            for chunk in stream:

                content = (
                    chunk.choices[0].delta.content
                )

                if content:

                    yield content

        return StreamingHttpResponse(
            generate(),
            content_type="text/plain"
        )


# =========================================
# AI RESUME ANALYZER
# =========================================

def resume(request):

    if request.method == "GET":
        return render(request, "tools/resume.html")

    if request.method == "POST":

        job_role = request.POST.get("job_role", "").strip()
        uploaded_file = request.FILES.get("resume")

        if not job_role:
            return StreamingHttpResponse(
                "Please enter the target job role."
            )

        if not uploaded_file:
            return StreamingHttpResponse(
                "Please upload your resume."
            )

        file_name = uploaded_file.name.lower()

        try:

            # =========================
            # READ PDF
            # =========================

            if file_name.endswith(".pdf"):

                reader = PdfReader(uploaded_file)

                resume_text = ""

                for page in reader.pages:
                    text = page.extract_text()

                    if text:
                        resume_text += text + "\n"


            # =========================
            # READ DOCX
            # =========================

            elif file_name.endswith(".docx"):

                document = Document(uploaded_file)

                resume_text = ""

                for paragraph in document.paragraphs:
                    resume_text += paragraph.text + "\n"


            # =========================
            # READ TXT
            # =========================

            elif file_name.endswith(".txt"):

                resume_text = uploaded_file.read().decode(
                    "utf-8",
                    errors="ignore"
                )


            else:

                return StreamingHttpResponse(
                    "Only PDF, DOCX and TXT files are supported."
                )


            if not resume_text.strip():

                return StreamingHttpResponse(
                    "Could not extract text from this resume. "
                    "Please upload a text-based PDF, DOCX or TXT file."
                )


            # =========================
            # AI RESUME ANALYSIS
            # =========================

            resume_prompt = f"""
You are a professional AI Resume Analyzer.

Analyze the following resume for the target job role.

TARGET JOB ROLE:
{job_role}

RESUME:
{resume_text}

Give a detailed resume analysis.

IMPORTANT:

1. Give an overall resume score from 0 to 100.
2. The score must be an AI-generated evaluation, not an official ATS score.
3. Evaluate the resume based on:
   - Skills
   - Education
   - Projects
   - Experience
   - Certifications
   - Keywords
   - Job-role relevance
   - Resume structure
   - Achievements
   - Professional presentation
4. Identify strengths.
5. Identify weaknesses.
6. Identify missing skills or keywords relevant to the target job role.
7. Give specific improvements required.
8. Give practical suggestions.
9. Suggest improvements to projects if needed.
10. Suggest improvements to technical skills if needed.
11. Suggest improvements to resume wording if needed.
12. Give job-role-specific recommendations.
13. Do not invent information about the candidate.
14. Do not claim that the score is from an official ATS system.

Return the answer using exactly these sections:

OVERALL RESUME SCORE:
[score]/100

SUMMARY:

STRENGTHS:

WEAKNESSES:

MISSING SKILLS AND KEYWORDS:

IMPROVEMENTS REQUIRED:

SUGGESTIONS:

JOB ROLE MATCH:

FINAL RECOMMENDATIONS:
"""

            stream = client.chat.completions.create(

                model="openai/gpt-oss-20b",

                messages=[
                    {
                        "role": "system",
                        "content":
                            "You are a professional AI Resume "
                            "Analysis assistant."
                    },
                    {
                        "role": "user",
                        "content": resume_prompt
                    }
                ],

                stream=True
            )


            # =========================
            # STREAM AI RESPONSE
            # =========================

            def generate():

                for chunk in stream:

                    content = chunk.choices[0].delta.content

                    if content:
                        yield content


            return StreamingHttpResponse(
                generate(),
                content_type="text/plain"
            )


        except Exception as e:

            return StreamingHttpResponse(
                "Error analyzing resume: " + str(e)
            )


def resume_guide(request):
    return render(
        request,
        "tools/resume_guide.html"
    )


def interview(request):

    return render(
        request,
        "tools/interview.html"
    )


# =========================================
# LOGIN
# =========================================

def login(request):

    # If already logged in, go directly to home
    if request.session.get("username"):
        return redirect("home")

    if request.method == "POST":

        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        try:

            existing_user = user.objects.get(
                username=username
            )

            if check_password(
                password,
                existing_user.password
            ):

                # Store username in session
                request.session["username"] = existing_user.username

                messages.success(
                    request,
                    "Login successful. Welcome to AI Tool Hub!"
                )

                return redirect("home")

            else:

                messages.error(
                    request,
                    "Invalid username or password."
                )

        except user.DoesNotExist:

            messages.error(
                request,
                "Invalid username or password."
            )

    return render(
        request,
        "tools/login.html"
    )


# =========================================
# SIGN UP
# =========================================

def signup(request):

    if request.method == "POST":

        name = request.POST.get("name", "").strip()
        username = request.POST.get("username", "").strip()
        email = request.POST.get("email", "").strip()
        mobile = request.POST.get("mobile", "").strip()
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")


        # =========================================
        # PASSWORD MATCH
        # =========================================

        if password != confirm_password:

            messages.error(
                request,
                "Passwords do not match."
            )

            return render(
                request,
                "tools/signup.html"
            )


        # =========================================
        # EMAIL VALIDATION
        # =========================================

        try:

            validate_email(email)

        except ValidationError:

            messages.error(
                request,
                "Please enter a valid email address."
            )

            return render(
                request,
                "tools/signup.html"
            )


        # =========================================
        # USERNAME CHECK
        # =========================================

        if user.objects.filter(
            username=username
        ).exists():

            messages.error(
                request,
                "Username already exists. Please choose another username."
            )

            return render(
                request,
                "tools/signup.html"
            )


        # =========================================
        # EMAIL CHECK
        # =========================================

        if user.objects.filter(
            email=email
        ).exists():

            messages.error(
                request,
                "Email already exists. Please use another email."
            )

            return render(
                request,
                "tools/signup.html"
            )


        # =========================================
        # CREATE USER
        # =========================================

        new_user = user.objects.create(

            name=name,

            username=username,

            email=email,

            mobile=mobile,

            password=make_password(password)

        )


        # =========================================
        # AUTOMATIC LOGIN AFTER SIGNUP
        # =========================================

        request.session["username"] = new_user.username


        messages.success(
            request,
            "Account created successfully. Welcome to AI Tool Hub!"
        )


        # Go directly to Home
        return redirect("home")


    # =========================================
    # GET REQUEST
    # =========================================

    return render(
        request,
        "tools/signup.html"
    )


# =========================================================
# PROFILE
# =========================================================

def profile(request):

    username = request.session.get("username")

    # If user is not logged in
    if not username:
        messages.error(
            request,
            "Please login to view your profile."
        )
        return redirect("login")

    try:

        current_user = user.objects.get(
            username=username
        )

    except user.DoesNotExist:

        request.session.flush()

        messages.error(
            request,
            "User account not found. Please login again."
        )

        return redirect("login")

    return render(
        request,
        "tools/profile.html",
        {
            "current_user": current_user
        }
    )


# =========================================================
# LOGOUT
# =========================================================

def logout(request):

    # Clear the user's session
    request.session.flush()

    messages.success(
        request,
        "You have been logged out successfully."
    )

    return redirect("login")
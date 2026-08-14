"use client";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const TOKEN_KEY = "edugen_token";
const USER_KEY = "edugen_user";

export type User = { id: number; email: string; created_at: string };

export type Topic = { topic: string; chapter: string; chunk_count: number };
export type Subject = {
  id: number;
  name: string;
  grade: number;
  topics: Topic[];
};

export type Question = {
  id: number;
  position: number;
  question_text: string;
  options: Record<string, string>;
  difficulty: string;
  source_chunk_ids: number[];
};

export type Adaptive = {
  proficiency_score: number;
  band: string;
  mix: Record<string, number>;
  rule: string;
  is_first_quiz_on_topic: boolean;
};

export type Quiz = {
  id: number;
  subject_id: number;
  subject: string;
  grade: number;
  topic: string;
  chapter: string;
  difficulty: string;
  created_at: string;
  adaptive: Adaptive;
  generation_provider: string;
  served_from_cache: boolean;
  questions: Question[];
  answered_question_ids: number[];
};

export type AnswerResponse = {
  question_id: number;
  selected_answer: string;
  correct_answer: string;
  is_correct: boolean;
  explanation: string;
  source_chunk_ids: number[];
  proficiency_score: number;
  proficiency_band: string;
  answers_count: number;
  counted: boolean;
};

export type ResultQuestion = {
  id: number;
  position: number;
  question_text: string;
  options: Record<string, string>;
  // Null until answered — the API does not reveal the key mid-quiz.
  correct_answer: string | null;
  selected_answer: string | null;
  is_correct: boolean | null;
  difficulty: string;
  explanation: string;
  source_chunk_ids: number[];
};

export type SourceChunk = {
  id: number;
  chapter: string;
  chunk_index: number;
  content: string;
};

export type QuizResult = {
  quiz_id: number;
  subject: string;
  grade: number;
  topic: string;
  chapter: string;
  difficulty: string;
  answered: number;
  total: number;
  correct: number;
  score_before: number;
  score_after: number;
  band_before: string;
  band_after: string;
  next_quiz_mix: Record<string, number>;
  questions: ResultQuestion[];
  sources: SourceChunk[];
};

export type ProficiencyTopic = {
  subject_id: number;
  subject: string;
  grade: number;
  topic: string;
  chapter: string;
  score: number;
  band: string;
  answers_count: number;
  next_quiz_mix: Record<string, number>;
  updated_at: string;
};

export type ProficiencyList = {
  user_id: number;
  formula: string;
  alpha: number;
  initial_score: number;
  topics: ProficiencyTopic[];
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function getUser(): User | null {
  if (typeof window === "undefined") return null;
  const raw = window.localStorage.getItem(USER_KEY);
  return raw ? (JSON.parse(raw) as User) : null;
}

export function setSession(token: string, user: User) {
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
}

async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers ?? {}),
    },
  });

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail[0]?.msg ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, detail);
  }

  return (await response.json()) as T;
}

export const api = {
  register: (email: string, password: string) =>
    request<{ access_token: string; user: User }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  login: (email: string, password: string) =>
    request<{ access_token: string; user: User }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),

  subjects: () => request<Subject[]>("/subjects"),

  quiz: (quizId: number) => request<Quiz>(`/quizzes/${quizId}`),

  createQuiz: (subjectId: number, topic: string) =>
    request<Quiz>("/quizzes", {
      method: "POST",
      body: JSON.stringify({ subject_id: subjectId, topic }),
    }),

  answer: (quizId: number, questionId: number, selected: string) =>
    request<AnswerResponse>(`/quizzes/${quizId}/answer`, {
      method: "POST",
      body: JSON.stringify({ question_id: questionId, selected_answer: selected }),
    }),

  results: (quizId: number) => request<QuizResult>(`/quizzes/${quizId}/results`),

  proficiency: (userId: number) =>
    request<ProficiencyList>(`/users/${userId}/proficiency`),
};

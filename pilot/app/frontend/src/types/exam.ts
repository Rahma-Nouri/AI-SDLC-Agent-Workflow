export type QuestionType = 'MULTIPLE_CHOICE' | 'SHORT_ANSWER';

export interface ExamQuestion {
  question_id: string;
  sequence_order: number;
  question_type: QuestionType;
  prompt: string;
  options: string[] | null;
  points: number;
}

export interface ExamDetails {
  exam_id: string;
  title: string;
  total_questions: number;
  questions: ExamQuestion[];
}

export interface SessionStartResponse {
  session_id: string;
  started_at: string;
}

export interface AnswerSubmissionItem {
  question_id: string;
  student_answer: string;
}

export interface SaveAnswersPayload {
  answers: AnswerSubmissionItem[];
}

export interface SaveAnswersResponse {
  status: string;
  last_saved_at: string;
}

export interface SubmitExamResponse {
  job_id: string;
}

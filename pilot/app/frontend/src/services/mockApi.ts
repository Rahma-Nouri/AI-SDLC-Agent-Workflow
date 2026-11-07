import { ExamDetails, SessionStartResponse, SaveAnswersPayload, SaveAnswersResponse, SubmitExamResponse } from '../types/exam';

const sampleExam: ExamDetails = {
  exam_id: 'exam_77d8e9f',
  title: 'Practice Midterm 1: Computer Systems',
  total_questions: 2,
  questions: [
    {
      question_id: 'q_1',
      sequence_order: 1,
      question_type: 'MULTIPLE_CHOICE',
      prompt: 'What is the primary function of the Program Counter register?',
      options: [
        'Holds current instruction',
        'Holds address of next instruction',
        'Stores ALU flags',
        'Buffers I/O data'
      ],
      points: 5
    },
    {
      question_id: 'q_2',
      sequence_order: 2,
      question_type: 'SHORT_ANSWER',
      prompt: 'Explain the difference between spatial and temporal locality.',
      options: null,
      points: 10
    }
  ]
};

export const mockApi = {
  async getExam(examId: string): Promise<ExamDetails> {
    return new Promise((resolve) => {
      setTimeout(() => resolve(sampleExam), 50);
    });
  },

  async startSession(examId: string): Promise<SessionStartResponse> {
    return new Promise((resolve) => {
      setTimeout(() => resolve({
        session_id: 'sess_102938',
        started_at: new Date().toISOString()
      }), 50);
    });
  },

  async saveAnswers(sessionId: string, payload: SaveAnswersPayload): Promise<SaveAnswersResponse> {
    return new Promise((resolve) => {
      setTimeout(() => resolve({
        status: 'SAVED',
        last_saved_at: new Date().toISOString()
      }), 50);
    });
  },

  async submitExam(sessionId: string): Promise<SubmitExamResponse> {
    return new Promise((resolve) => {
      setTimeout(() => resolve({
        job_id: 'job_eval_4455'
      }), 50);
    });
  }
};

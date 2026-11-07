import React, { useEffect, useState } from 'react';
import { ExamDetails, QuestionType } from '../types/exam';
import { mockApi } from '../services/mockApi';

interface PracticeExamViewProps {
  examId: string;
}

const ADVISORY_TEXT = "AI feedback is advisory. Review model answers for authoritative validation.";

export const PracticeExamView: React.FC<PracticeExamViewProps> = ({ examId }) => {
  const [exam, setExam] = useState<ExamDetails | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [currentIndex, setCurrentIndex] = useState<number>(0);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [saveStatus, setSaveStatus] = useState<string>('');
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [submittedJobId, setSubmittedJobId] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const storageKey = `exam_answers_${examId}`;

  useEffect(() => {
    const initializeExam = async () => {
      try {
        setLoading(true);
        const examData = await mockApi.getExam(examId);
        setExam(examData);

        const session = await mockApi.startSession(examId);
        setSessionId(session.session_id);

        const cachedAnswers = localStorage.getItem(storageKey);
        if (cachedAnswers) {
          try {
            setAnswers(JSON.parse(cachedAnswers));
          } catch (e) {
            console.error('Failed to parse cached answers', e);
          }
        }
      } catch (err) {
        setError('Failed to initialize exam session.');
      } finally {
        setLoading(false);
      }
    };

    initializeExam();
  }, [examId, storageKey]);

  const handleAnswerChange = (questionId: string, value: string) => {
    const updated = { ...answers, [questionId]: value };
    setAnswers(updated);
    localStorage.setItem(storageKey, JSON.stringify(updated));
  };

  const handleSaveProgress = async () => {
    if (!sessionId) return;
    try {
      setSaveStatus('Saving...');
      const payload = {
        answers: Object.entries(answers).map(([question_id, student_answer]) => ({
          question_id,
          student_answer
        }))
      };
      const res = await mockApi.saveAnswers(sessionId, payload);
      setSaveStatus(`Saved at ${new Date(res.last_saved_at).toLocaleTimeString()}`);
    } catch (err) {
      setSaveStatus('Failed to save progress.');
    }
  };

  const handleSubmit = async () => {
    if (!sessionId) return;
    try {
      setIsSubmitting(true);
      await handleSaveProgress();
      const result = await mockApi.submitExam(sessionId);
      setSubmittedJobId(result.job_id);
      localStorage.removeItem(storageKey);
    } catch (err) {
      setError('Submission failed. Please retry.');
    } finally {
      setIsSubmitting(false);
    }
  };

  if (loading) return <div>Loading exam session...</div>;
  if (error) return <div role="alert">{error}</div>;
  if (!exam) return <div>Exam not found.</div>;

  if (submittedJobId) {
    return (
      <section aria-label="Submission Confirmation">
        <h2>Exam Submitted Successfully</h2>
        <p>Your submission job ID: <strong>{submittedJobId}</strong></p>
        <p className="advisory-notice">{ADVISORY_TEXT}</p>
      </section>
    );
  }

  const currentQuestion = exam.questions[currentIndex];
  const isLastQuestion = currentIndex === exam.questions.length - 1;

  return (
    <div className="exam-container" style={{ maxWidth: '800px', margin: '0 auto', padding: '1rem' }}>
      <header>
        <h1>{exam.title}</h1>
        <p className="advisory-notice" style={{ color: '#666', fontStyle: 'italic', marginBottom: '1rem' }}>
          {ADVISORY_TEXT}
        </p>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '1rem' }}>
          <span>Question {currentIndex + 1} of {exam.total_questions}</span>
          <span>Session: {sessionId}</span>
        </div>
      </header>

      <main>
        {currentQuestion && (
          <div key={currentQuestion.question_id} style={{ border: '1px solid #ccc', padding: '1.5rem', borderRadius: '8px' }}>
            <h3>{currentQuestion.prompt} ({currentQuestion.points} points)</h3>

            {currentQuestion.question_type === 'MULTIPLE_CHOICE' && currentQuestion.options && (
              <fieldset style={{ border: 'none', padding: 0 }}>
                <legend className="sr-only">Options</legend>
                {currentQuestion.options.map((option, idx) => (
                  <div key={idx} style={{ marginBottom: '0.5rem' }}>
                    <label>
                      <input
                        type="radio"
                        name={`question_${currentQuestion.question_id}`}
                        value={option}
                        checked={answers[currentQuestion.question_id] === option}
                        onChange={(e) => handleAnswerChange(currentQuestion.question_id, e.target.value)}
                      />
                      {' '}{option}
                    </label>
                  </div>
                ))}
              </fieldset>
            )}

            {currentQuestion.question_type === 'SHORT_ANSWER' && (
              <div style={{ marginTop: '1rem' }}>
                <label htmlFor={`answer_${currentQuestion.question_id}`}>Your Answer:</label>
                <textarea
                  id={`answer_${currentQuestion.question_id}`}
                  rows={5}
                  style={{ width: '100%', marginTop: '0.5rem' }}
                  value={answers[currentQuestion.question_id] || ''}
                  onChange={(e) => handleAnswerChange(currentQuestion.question_id, e.target.value)}
                  placeholder="Type your answer here..."
                />
              </div>
            )}
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '1.5rem' }}>
          <div>
            <button
              type="button"
              onClick={() => setCurrentIndex((prev) => Math.max(prev - 1, 0))}
              disabled={currentIndex === 0}
            >
              Previous
            </button>
            <button
              type="button"
              onClick={() => setCurrentIndex((prev) => Math.min(prev + 1, exam.questions.length - 1))}
              disabled={isLastQuestion}
              style={{ marginLeft: '0.5rem' }}
            >
              Next
            </button>
          </div>

          <div>
            <button type="button" onClick={handleSaveProgress} style={{ marginRight: '0.5rem' }}>
              Save Progress
            </button>
            {isLastQuestion ? (
              <button
                type="button"
                onClick={handleSubmit}
                disabled={isSubmitting}
                style={{ backgroundColor: '#0066cc', color: 'white', fontWeight: 'bold' }}
              >
                {isSubmitting ? 'Submitting...' : 'Submit Exam'}
              </button>
            ) : null}
          </div>
        </div>

        {saveStatus && <p aria-live="polite" style={{ marginTop: '0.5rem', color: '#2d6a4f' }}>{saveStatus}</p>}
      </main>
    </div>
  );
};

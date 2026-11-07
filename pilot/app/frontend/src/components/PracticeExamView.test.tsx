import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, beforeEach } from 'vitest';
import React from 'react';
import { PracticeExamView } from './PracticeExamView';

describe('PracticeExamView', () => {
  const examId = 'exam_77d8e9f';

  beforeEach(() => {
    localStorage.clear();
  });

  it('renders exam questions and required advisory disclaimer', async () => {
    render(<PracticeExamView examId={examId} />);

    expect(await screen.findByText('Practice Midterm 1: Computer Systems')).toBeInTheDocument();
    expect(
      screen.getByText('AI feedback is advisory. Review model answers for authoritative validation.')
    ).toBeInTheDocument();
    expect(
      screen.getByText('What is the primary function of the Program Counter register? (5 points)')
    ).toBeInTheDocument();
  });

  it('tracks student responses and preserves them in localStorage', async () => {
    render(<PracticeExamView examId={examId} />);

    const mcOption = await screen.findByLabelText('Holds address of next instruction');
    fireEvent.click(mcOption);
    expect(mcOption).toBeChecked();

    const cached = JSON.parse(localStorage.getItem(`exam_answers_${examId}`) || '{}');
    expect(cached['q_1']).toBe('Holds address of next instruction');
  });

  it('navigates to the next question and renders text inputs for open-ended questions', async () => {
    render(<PracticeExamView examId={examId} />);

    const nextButton = await screen.findByRole('button', { name: /Next/i });
    fireEvent.click(nextButton);

    expect(
      await screen.findByText('Explain the difference between spatial and temporal locality. (10 points)')
    ).toBeInTheDocument();

    const textArea = screen.getByLabelText(/Your Answer:/i);
    fireEvent.change(textArea, { target: { value: 'Spatial is locality in space; temporal is in time.' } });
    expect(textArea).toHaveValue('Spatial is locality in space; temporal is in time.');

    const cached = JSON.parse(localStorage.getItem(`exam_answers_${examId}`) || '{}');
    expect(cached['q_2']).toBe('Spatial is locality in space; temporal is in time.');
  });

  it('allows submitting the exam session on the final question', async () => {
    render(<PracticeExamView examId={examId} />);

    const nextButton = await screen.findByRole('button', { name: /Next/i });
    fireEvent.click(nextButton);

    const submitButton = await screen.findByRole('button', { name: /Submit Exam/i });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(screen.getByText(/Exam Submitted Successfully/i)).toBeInTheDocument();
      expect(screen.getByText('job_eval_4455')).toBeInTheDocument();
    });
  });
});

import React from 'react';
import { PracticeExamView } from './components/PracticeExamView';

export const App: React.FC = () => {
  return (
    <div className="app-root">
      <PracticeExamView examId="exam_77d8e9f" />
    </div>
  );
};

export default App;

# AI-Powered Adaptive Exam Preparation System

## 1. Project Overview

The pilot project is an AI-powered exam preparation system designed to help university students prepare for exams using their own study materials and previous or example exam questions.

The system will be used as a concrete software project around which the AI-supported software development workflow will be designed and demonstrated. The purpose of the pilot is not only to create a useful student application, but also to provide a realistic software project that can be processed by the different AI agents in the development workflow.

The system will initially focus on generating practice exams that are similar in structure and difficulty to example or previous exams. It will later be extended to support broader study-material analysis and adaptive exam preparation.

## 2. Main Problem

Students often receive example exams or previous examination questions from their professors. These examples can give students an idea of the expected topics, question types, structure, and difficulty of the actual exam.

However, students usually have to manually analyze these materials and create additional practice questions themselves.

The system aims to help students by automatically analyzing example exams and study materials and generating useful preparation content.

## 3. Initial Core Functionality

The first version of the system will focus on the following workflow:

1. The student uploads an example exam, previous exam, or a list of exam questions.
2. The system analyzes the uploaded questions.
3. The system identifies the main topics, question types, structure, and approximate difficulty.
4. The student requests a new practice exam.
5. The system generates a new exam with questions similar in style, structure, topics, and difficulty to the provided example.
6. The student can complete the generated practice exam.
7. The system evaluates the student's answers when possible and provides feedback.
8. The student can generate another practice exam for additional preparation.

The generated exam should not simply copy the original questions. It should create new questions that are relevant to the same subject and follow a similar pattern.

## 4. Extended Study Preparation Functionality

After the initial exam-generation functionality is implemented, the system can be extended to support additional study materials.

Students may upload:

* Lecture notes
* PDF study materials
* Course slides
* Previous exams
* Example questions
* Lists of questions
* Other relevant course materials

The system can analyze these materials and provide:

### Summaries

Generate concise summaries of the uploaded study materials and identify the most important concepts.

### Key Points

Generate a list of important facts, concepts, definitions, formulas, or topics that the student should remember before the exam.

### Practice Questions

Generate different types of practice questions based on the uploaded materials.

### Practice Exams

Generate complete example exams based on the student's materials and previous/example exams.

### Readiness Checking

The system should allow students to complete practice exams and receive an indication of which topics they understand well and which topics require additional study.

## 5. Adaptive Preparation

In a later version, the system may adapt future practice activities according to the student's performance.

For example:

* If the student performs well on a topic, fewer questions may be generated for that topic.
* If the student repeatedly makes mistakes on a topic, additional questions may be generated.
* The difficulty of questions may be increased or decreased according to performance.
* The system may recommend topics that require further study.

The goal is to move from simple question generation toward personalized exam preparation.

## 6. Example User Scenario

A professor provides students with an example exam before the final examination.

A student uploads the example exam to the system and asks:

> "Generate another practice exam with similar topics, question types, structure, and difficulty."

The system analyzes the example and generates a new practice exam without simply copying the original questions.

The student completes the generated exam and receives feedback about their performance.

The student can then upload lecture notes and other study materials to receive summaries, important points, and additional practice questions.

## 7. Initial System Components

The pilot system is expected to contain the following high-level components:

* User interface
* Study material and exam upload functionality
* Document/question processing
* Content and question analysis
* AI-based question generation
* Practice exam generation
* Answer evaluation
* Performance tracking
* Study summary generation
* Key-point extraction
* Basic readiness assessment

The exact architecture and technology choices will be determined during the AI-supported software development workflow.

## 8. Main Goal

The main goal of the pilot project is to provide students with an AI-supported tool that can transform their existing course materials and example exams into personalized preparation resources.

At the same time, the project will serve as a concrete case study for the AI-supported software development workflow. The project specification will be provided to the Project Manager Agent, which will analyze the requirements and decompose them into structured development tasks for subsequent agents.

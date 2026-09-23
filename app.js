/* =========================================================
   AUTOGRADE
   Frontend
========================================================= */


/* =========================================================
   GLOBAL STATE
========================================================= */

let gradingResults = [];

let currentRubric = null;

let rubricApproved = false;


/* =========================================================
   NAVIGATION
========================================================= */

function showSection(
    sectionId,
    clickedButton
) {

    const sections =
        document.querySelectorAll(
            ".page-section"
        );


    sections.forEach(
        section => {

            section.classList.remove(
                "active-section"
            );

        }
    );


    const selectedSection =
        document.getElementById(
            sectionId
        );


    if (selectedSection) {

        selectedSection.classList.add(
            "active-section"
        );

    }


    const navButtons =
        document.querySelectorAll(
            ".nav-button"
        );


    navButtons.forEach(
        button => {

            button.classList.remove(
                "active"
            );

        }
    );


    if (clickedButton) {

        clickedButton.classList.add(
            "active"
        );

    }

}


/* =========================================================
   FILE SELECTION
========================================================= */

function showSelectedFile(
    inputId,
    outputId
) {

    const input =
        document.getElementById(
            inputId
        );


    const output =
        document.getElementById(
            outputId
        );


    if (!input || !output) {

        return;

    }


    if (
        input.files.length === 0
    ) {

        output.textContent =
            "No file selected";

        return;

    }


    output.textContent =
        "Selected: " +
        input.files[0].name;

}


/* =========================================================
   GENERATE RUBRIC
========================================================= */

async function generateRubric() {

    const questionPaperInput =
        document.getElementById(
            "questionPaper"
        );


    const textbookInput =
        document.getElementById(
            "textbook"
        );


    const rubricArea =
        document.getElementById(
            "rubricArea"
        );


    const approvalMessage =
        document.getElementById(
            "approvalMessage"
        );


    if (
        !questionPaperInput ||
        !questionPaperInput.files.length
    ) {

        alert(
            "Please upload the question paper."
        );

        return;

    }


    const questionPaper =
        questionPaperInput.files[0];


    const textbook =
        textbookInput &&
        textbookInput.files.length

            ? textbookInput.files[0]

            : null;


    const allowedTypes = [

        "application/pdf",

        "image/jpeg",

        "image/png"

    ];


    if (
        !allowedTypes.includes(
            questionPaper.type
        )
    ) {

        alert(
            "Question paper must be PDF, JPG, JPEG or PNG."
        );

        return;

    }


    if (
        textbook &&
        !allowedTypes.includes(
            textbook.type
        )
    ) {

        alert(
            "Textbook must be PDF, JPG, JPEG or PNG."
        );

        return;

    }


    const formData =
        new FormData();


    formData.append(
        "question_paper",
        questionPaper
    );


    if (textbook) {

        formData.append(
            "textbook",
            textbook
        );

    }


    const button =
        document.querySelector(
            "#setup .primary-button"
        );


    if (button) {

        button.disabled = true;

        button.textContent =
            "Generating Rubric...";

    }


    approvalMessage.classList.add(
        "hidden"
    );


    rubricArea.classList.add(
        "hidden"
    );


    let progress =
        document.getElementById(
            "rubricProgress"
        );


    if (!progress) {

        progress =
            document.createElement(
                "div"
            );


        progress.id =
            "rubricProgress";


        progress.className =
            "success-message";


        const setupPanel =
            document.querySelector(
                "#setup .setup-panel"
            );


        if (setupPanel) {

            setupPanel.appendChild(
                progress
            );

        }

    }


    progress.textContent =
        "⏳ Preparing files...";


    progress.classList.remove(
        "hidden"
    );


    try {

        await sleep(400);


        progress.textContent =
            "⏳ Uploading files to Gemini...";


        const response =
            await fetch(
                "http://127.0.0.1:8000/generate-rubric",
                {

                    method: "POST",

                    body: formData

                }
            );


        progress.textContent =
            "⏳ Gemini is reading the question paper...";


        let result;


        try {

            result =
                await response.json();

        }
        catch {

            throw new Error(
                "Backend returned an invalid response."
            );

        }


        if (
            response.status === 429
        ) {

            throw new Error(
                "Gemini is currently rate-limited. " +
                "Please wait and try again."
            );

        }


        if (
            response.status === 503
        ) {

            throw new Error(
                "Gemini is temporarily experiencing high demand. " +
                "Please wait and try again."
            );

        }


        if (!response.ok) {

            throw new Error(
                result.detail ||
                "Rubric generation failed."
            );

        }


        if (!result.rubric) {

            throw new Error(
                "No rubric was returned."
            );

        }


        currentRubric =
            result.rubric;


        rubricApproved =
            false;


        progress.textContent =
            "✓ Rubric generated successfully. Review it below.";


        displayGeneratedRubric(
            result.rubric
        );


    }
    catch (error) {

        console.error(
            error
        );


        progress.textContent =
            "✗ Rubric generation failed.";


        alert(
            "Error generating rubric:\n\n" +
            error.message
        );

    }
    finally {

        if (button) {

            button.disabled = false;

            button.textContent =
                "Generate Rubric";

        }

    }

}


/* =========================================================
   DISPLAY GENERATED RUBRIC
========================================================= */

function displayGeneratedRubric(
    rubric
) {

    currentRubric =
        rubric;


    const rubricArea =
        document.getElementById(
            "rubricArea"
        );


    if (!rubricArea) {

        return;

    }


    rubricArea.innerHTML =
        "";


    const title =
        document.createElement(
            "h4"
        );


    title.textContent =
        rubric.exam_title ||
        "Generated Rubric";


    rubricArea.appendChild(
        title
    );


    if (
        !rubric.questions ||
        rubric.questions.length === 0
    ) {

        const message =
            document.createElement(
                "p"
            );


        message.className =
            "file-name";


        message.textContent =
            "No questions were detected.";


        rubricArea.appendChild(
            message
        );


        rubricArea.classList.remove(
            "hidden"
        );


        return;

    }


    rubric.questions.forEach(
        question => {

            const questionBlock =
                document.createElement(
                    "div"
                );


            questionBlock.className =
                "generated-question";


            const header =
                document.createElement(
                    "div"
                );


            header.className =
                "generated-question-header";


            const questionInfo =
                document.createElement(
                    "div"
                );


            const number =
                document.createElement(
                    "strong"
                );


            number.textContent =
                "Q" +
                question.question_number;


            const text =
                document.createElement(
                    "p"
                );


            text.textContent =
                question.question_text;


            questionInfo.appendChild(
                number
            );


            questionInfo.appendChild(
                text
            );


            const meta =
                document.createElement(
                    "div"
                );


            meta.className =
                "question-meta";


            const type =
                document.createElement(
                    "span"
                );


            type.textContent =
                question.question_type;


            const marks =
                document.createElement(
                    "strong"
                );


            marks.textContent =
                question.total_marks +
                (
                    question.total_marks === 1
                        ? " Mark"
                        : " Marks"
                );


            meta.appendChild(
                type
            );


            meta.appendChild(
                marks
            );


            header.appendChild(
                questionInfo
            );


            header.appendChild(
                meta
            );


            questionBlock.appendChild(
                header
            );


            const criteria =
                document.createElement(
                    "div"
                );


            criteria.className =
                "generated-criteria";


            if (
                question.criteria &&
                question.criteria.length
            ) {

                question.criteria.forEach(
                    criterion => {

                        const row =
                            document.createElement(
                                "div"
                            );


                        row.className =
                            "generated-criterion";


                        const left =
                            document.createElement(
                                "div"
                            );


                        const criterionName =
                            document.createElement(
                                "b"
                            );


                        criterionName.textContent =
                            criterion.criterion;


                        const description =
                            document.createElement(
                                "p"
                            );


                        description.textContent =
                            criterion.description;


                        left.appendChild(
                            criterionName
                        );


                        left.appendChild(
                            description
                        );


                        const criterionMarks =
                            document.createElement(
                                "strong"
                            );


                        criterionMarks.textContent =
                            criterion.marks +
                            (
                                criterion.marks === 1
                                    ? " Mark"
                                    : " Marks"
                            );


                        row.appendChild(
                            left
                        );


                        row.appendChild(
                            criterionMarks
                        );


                        criteria.appendChild(
                            row
                        );

                    }
                );

            }


            questionBlock.appendChild(
                criteria
            );


            rubricArea.appendChild(
                questionBlock
            );

        }
    );


    const approveButton =
        document.createElement(
            "button"
        );


    approveButton.className =
        "approve-button";


    approveButton.textContent =
        "✓ Approve Rubric";


    approveButton.addEventListener(
        "click",
        approveRubric
    );


    rubricArea.appendChild(
        approveButton
    );


    rubricArea.classList.remove(
        "hidden"
    );


    displayQuestionRouting(
        rubric.questions
    );

}


/* =========================================================
   QUESTION TYPE ROUTING
========================================================= */

function displayQuestionRouting(
    questions
) {

    const routingPanel =
        document.getElementById(
            "questionRouting"
        );


    const routingList =
        document.getElementById(
            "routingList"
        );


    if (
        !routingPanel ||
        !routingList
    ) {

        return;

    }


    routingList.innerHTML =
        "";


    let theoryCount = 0;

    let diagramCount = 0;

    let numericalCount = 0;


    questions.forEach(
        question => {

            const type =
                normalizeQuestionType(
                    question.question_type
                );


            if (
                type === "Theory"
            ) {

                theoryCount++;

            }


            if (
                type === "Diagram"
            ) {

                diagramCount++;

            }


            if (
                type === "Numerical"
            ) {

                numericalCount++;

            }


            let nextAgent;


            if (
                type === "Numerical"
            ) {

                nextAgent =
                    "Math Agent";

            }
            else {

                nextAgent =
                    "Grading Agent";

            }


            const card =
                document.createElement(
                    "div"
                );


            card.className =
                "routing-question";


            card.innerHTML = `

                <div class="routing-question-header">

                    <div>

                        <div class="routing-question-number">
                            Q${escapeHtml(
                                question.question_number
                            )}
                        </div>

                        <div class="routing-question-text">
                            ${escapeHtml(
                                question.question_text
                            )}
                        </div>

                    </div>

                    <span class="routing-type">
                        ${escapeHtml(type)}
                    </span>

                </div>


                <div class="routing-path">

                    <div>

                        <strong>
                            Classified as ${escapeHtml(type)}
                        </strong>

                    </div>

                    <div class="routing-arrow">
                        →
                    </div>

                    <div>

                        <div class="routing-agent">
                            ${escapeHtml(nextAgent)}
                        </div>

                        <div class="routing-description">
                            ${type === "Numerical"
                                ? "Mathematical verification path"
                                : "Rubric-based grading path"
                            }
                        </div>

                    </div>

                </div>

            `;


            routingList.appendChild(
                card
            );

        }
    );


    const summary =
        document.createElement(
            "div"
        );


    summary.className =
        "routing-summary";


    summary.innerHTML = `

        <div class="routing-summary-card">

            <strong>
                ${theoryCount}
            </strong>

            <span>
                Theory
            </span>

        </div>


        <div class="routing-summary-card">

            <strong>
                ${diagramCount}
            </strong>

            <span>
                Diagram
            </span>

        </div>


        <div class="routing-summary-card">

            <strong>
                ${numericalCount}
            </strong>

            <span>
                Numerical
            </span>

        </div>

    `;


    routingList.appendChild(
        summary
    );


    routingPanel.classList.remove(
        "hidden"
    );

}


/* =========================================================
   APPROVE RUBRIC
   DATABASE INTENTIONALLY DISABLED
========================================================= */

function approveRubric() {

    if (!currentRubric) {

        alert(
            "No rubric is available."
        );

        return;

    }


    rubricApproved =
        true;


    const approvalMessage =
        document.getElementById(
            "approvalMessage"
        );


    approvalMessage.textContent =
        "✓ Rubric approved for this session.";


    approvalMessage.classList.remove(
        "hidden"
    );


    const button =
        document.querySelector(
            "#rubricArea .approve-button"
        );


    if (button) {

        button.textContent =
            "✓ Rubric Approved";

        button.disabled =
            true;

    }


    console.log(
        "Approved rubric:",
        currentRubric
    );

}


/* =========================================================
   REAL ANSWER SHEET OCR
========================================================= */

async function startGrading() {

    const answerSheetInput =
        document.getElementById(
            "answerSheet"
        );


    if (
        !answerSheetInput ||
        !answerSheetInput.files.length
    ) {

        alert(
            "Please upload a handwritten answer sheet."
        );

        return;

    }


    if (!currentRubric) {

        alert(
            "Please generate the rubric first."
        );

        return;

    }


    if (!rubricApproved) {

        alert(
            "Please approve the rubric first."
        );

        return;

    }


    const answerSheet =
        answerSheetInput.files[0];


    const allowedTypes = [

        "application/pdf",

        "image/jpeg",

        "image/png"

    ];


    if (
        !allowedTypes.includes(
            answerSheet.type
        )
    ) {

        alert(
            "Answer sheet must be PDF, JPG, JPEG or PNG."
        );

        return;

    }


    /* =====================================================
       FORM DATA
    ===================================================== */

    const formData =
        new FormData();


    formData.append(
        "answer_sheet",
        answerSheet
    );


    formData.append(
        "rubric",
        JSON.stringify(
            currentRubric
        )
    );


    /* =====================================================
       UI
    ===================================================== */

    const button =
        document.querySelector(
            "#grading .primary-button"
        );


    if (button) {

        button.disabled = true;

        button.textContent =
            "Processing Answer Sheet...";

    }


    const status =
        document.getElementById(
            "processingStatus"
        );


    if (status) {

        status.textContent =
            "Running grading pipeline...";

    }


    resetProcessingSteps();


    setProcessingRunning(
        1
    );


    try {

        /* =================================================
           CALL REAL BACKEND
        ================================================= */

        const response =
            await fetch(

                "http://127.0.0.1:8000/process-answer-sheet",

                {

                    method:
                        "POST",

                    body:
                        formData

                }

            );


        let result;


        try {

            result =
                await response.json();

        }
        catch {

            throw new Error(
                "Backend returned an invalid response."
            );

        }


        if (!response.ok) {

            throw new Error(

                result.detail ||

                "Answer processing failed."

            );

        }


        /* =================================================
           MARK PIPELINE STAGES
        ================================================= */

        markProcessingDone(1);

        markProcessingDone(2);

        markProcessingDone(3);

        markProcessingDone(4);

        markProcessingDone(5);

        markProcessingDone(6);


        /* =================================================
           DISPLAY RESULTS
        ================================================= */

        displayFinalGradingResults(
            result
        );


        if (status) {

            status.textContent =
                "Completed";

        }


        console.log(
            "AutoGrade pipeline result:",
            result
        );


    }
    catch (error) {

        console.error(
            error
        );


        if (status) {

            status.textContent =
                "Failed";

        }


        alert(

            "Error processing answer sheet:\n\n" +

            error.message

        );

    }
    finally {

        if (button) {

            button.disabled = false;

            button.textContent =
                "Process Answer Sheet";

        }

    }

}
function displayFinalGradingResults(
    result
) {

    const panel =
        document.getElementById(
            "finalGradingPanel"
        );


    const summary =
        document.getElementById(
            "finalGradingSummary"
        );


    const questions =
        document.getElementById(
            "finalQuestionResults"
        );


    if (
        !panel ||
        !summary ||
        !questions
    ) {

        return;

    }


    summary.innerHTML = `

        <div class="grading-summary-card">

            <span>
                Total Score
            </span>

            <strong>
                ${result.total_score}
                /
                ${result.total_marks}
            </strong>

        </div>

        <div class="grading-summary-card">

            <span>
                Questions Processed
            </span>

            <strong>
                ${result.questions.length}
            </strong>

        </div>

    `;


    questions.innerHTML =
        "";


    result.questions.forEach(
        question => {

            const card =
                document.createElement(
                    "div"
                );


            card.className =
                "final-question-card";


            let confidence =
                question.confidence;


            let decisionClass =
                "";


            if (
                question.decision ===
                "AUTO ACCEPT"
            ) {

                decisionClass =
                    "decision-auto";

            }
            else if (
                question.decision ===
                "FLAGGED"
            ) {

                decisionClass =
                    "decision-flagged";

            }
            else {

                decisionClass =
                    "decision-manual";

            }


            let gradingHTML =
                "";


            if (
                question.grading
            ) {

                gradingHTML = `

                    <div class="grading-details">

                        <h4>
                            Criterion Grading
                        </h4>

                        ${
                            question.grading
                            .criterion_results
                            .map(
                                item => `

                                <div class="criterion-result">

                                    <div>

                                        <b>
                                            ${escapeHtml(
                                                item.criterion
                                            )}
                                        </b>

                                        <p>
                                            ${escapeHtml(
                                                item.reason
                                            )}
                                        </p>

                                    </div>

                                    <strong>

                                        ${item.marks_awarded}
                                        /
                                        ${item.max_marks}

                                    </strong>

                                </div>

                            `
                            )
                            .join("")
                        }

                    </div>

                `;

            }


            let criticHTML =
                "";


            if (
                question.critic
            ) {

                criticHTML = `

                    <div class="agent-check">

                        <b>
                            Critic Agent
                        </b>

                        <span>
                            ${escapeHtml(
                                question.critic.decision
                            )}
                        </span>

                        <p>
                            ${escapeHtml(
                                question.critic.reason
                            )}
                        </p>

                    </div>

                `;

            }


            let resolverHTML =
                "";


            if (
                question.resolver
            ) {

                resolverHTML = `

                    <div class="agent-check">

                        <b>
                            Resolver
                        </b>

                        <span>
                            ${escapeHtml(
                                question.resolver.status
                            )}
                        </span>

                    </div>

                `;

            }


            let mathHTML =
                "";


            if (
                question.math
            ) {

                const math =
                    question.math;


                const verification =
                    math.verification;


                mathHTML = `

                    <div class="math-details">

                        <h4>
                            Mathematical Verification
                        </h4>

                        <p>

                            <b>
                                Pix2Text:
                            </b>

                            ${escapeHtml(
                                math.pix2text
                            )}

                        </p>

                        <p>

                            <b>
                                SymPy:
                            </b>

                            ${escapeHtml(
                                verification.message
                            )}

                        </p>

                    </div>

                `;

            }


            let evidenceHTML =
                "";


            if (
                question.evidence
            ) {

                evidenceHTML = `

                    <div class="evidence-details">

                        <h4>
                            Evidence Verification
                        </h4>

                        <p>

                            Evidence confidence:

                            ${(
                                question.evidence.c_evid
                                * 100
                            ).toFixed(1)}%

                        </p>

                        <p>

                            Status:

                            ${
                                question.evidence.valid
                                    ? "Verified"
                                    : "Not fully verified"
                            }

                        </p>

                    </div>

                `;

            }


            card.innerHTML = `

                <div class="final-question-header">

                    <div>

                        <div class="final-question-number">

                            Q${escapeHtml(
                                question.question_number
                            )}

                        </div>

                        <div class="final-question-text">

                            ${escapeHtml(
                                question.question_text
                            )}

                        </div>

                    </div>

                    <span class="routing-type">

                        ${escapeHtml(
                            question.question_type
                        )}

                    </span>

                </div>


                <div class="answer-display">

                    <h4>
                        Student Answer
                    </h4>

                    <p>
                        ${escapeHtml(
                            question.answer_text
                        )}
                    </p>

                </div>


                ${
                    question.score !== null
                        ? `

                            <div class="score-display">

                                <span>
                                    Score
                                </span>

                                <strong>

                                    ${question.score}
                                    /
                                    ${question.max_marks}

                                </strong>

                            </div>

                          `
                        : ""
                }


                ${gradingHTML}

                ${criticHTML}

                ${resolverHTML}

                ${mathHTML}

                ${evidenceHTML}


                <div class="confidence-details">

                    <h4>
                        Confidence Engine
                    </h4>

                    <div class="confidence-grid">

                        <span>
                            OCR:
                            ${(confidence.c_ocr * 100).toFixed(1)}%
                        </span>

                        <span>
                            Agreement:
                            ${(confidence.c_agree * 100).toFixed(1)}%
                        </span>

                        <span>
                            Evidence:
                            ${(confidence.c_evid * 100).toFixed(1)}%
                        </span>

                        <span>
                            Math:
                            ${(confidence.c_math * 100).toFixed(1)}%
                        </span>

                        <strong>
                            Final:
                            ${(confidence.c_final * 100).toFixed(1)}%
                        </strong>

                    </div>

                </div>


                <div class="final-decision ${decisionClass}">

                    ${escapeHtml(
                        question.decision
                    )}

                </div>

            `;


            questions.appendChild(
                card
            );

        }
    );


    panel.classList.remove(
        "hidden"
    );

}

/* =========================================================
   DISPLAY OCR RESULTS
========================================================= */

function displayOCRResults(
    answers
) {

    const panel =
        document.getElementById(
            "ocrAnswerPanel"
        );


    const container =
        document.getElementById(
            "ocrAnswerList"
        );


    if (
        !panel ||
        !container
    ) {

        return;

    }


    container.innerHTML =
        "";


    if (
        answers.length === 0
    ) {

        container.innerHTML = `

            <div class="empty-state">

                <h3>
                    No answers detected
                </h3>

                <p>
                    Gemini could not identify any question answers.
                </p>

            </div>

        `;


        panel.classList.remove(
            "hidden"
        );


        return;

    }


    answers.forEach(
        answer => {

            const card =
                document.createElement(
                    "div"
                );


            card.className =
                "ocr-answer-card";


            const type =
                normalizeQuestionType(
                    answer.question_type
                );


            const nextAgent =
                type === "Numerical"

                    ? "Math Agent"

                    : "Grading Agent";


            const heading =
                document.createElement(
                    "div"
                );


            heading.className =
                "ocr-answer-heading";


            heading.innerHTML = `

                <div>

                    <strong>
                        Q${escapeHtml(
                            answer.question_number
                        )}
                    </strong>

                    <span class="ocr-type">
                        ${escapeHtml(type)}
                    </span>

                </div>

                <span class="ocr-next-agent">
                    Next → ${escapeHtml(nextAgent)}
                </span>

            `;


            const answerTitle =
                document.createElement(
                    "h4"
                );


            answerTitle.textContent =
                "Extracted Answer";


            const answerText =
                document.createElement(
                    "p"
                );


            answerText.className =
                "ocr-answer-text";


            answerText.textContent =
                answer.answer_text ||
                "No readable answer text found.";


            card.appendChild(
                heading
            );


            card.appendChild(
                answerTitle
            );


            card.appendChild(
                answerText
            );


            if (
                answer.contains_diagram
            ) {

                const diagram =
                    document.createElement(
                        "div"
                    );


                diagram.className =
                    "ocr-warning"
                ;


                diagram.textContent =
                    "Diagram detected in this answer.";


                card.appendChild(
                    diagram
                );

            }


            if (
                answer.illegible_regions &&
                answer.illegible_regions.length
            ) {

                const warning =
                    document.createElement(
                        "div"
                    );


                warning.className =
                    "ocr-warning";


                warning.innerHTML =
                    "<strong>Illegible regions:</strong> " +
                    escapeHtml(
                        answer.illegible_regions.join(
                            " | "
                        )
                    );


                card.appendChild(
                    warning
                );

            }


            container.appendChild(
                card
            );

        }
    );


    panel.classList.remove(
        "hidden"
    );

}


/* =========================================================
   PROCESSING STATUS HELPERS
========================================================= */

function resetProcessingSteps() {

    for (
        let i = 1;
        i <= 6;
        i++
    ) {

        const step =
            document.getElementById(
                "process" + i
            );


        if (!step) {

            continue;

        }


        step.classList.remove(
            "running"
        );


        step.classList.remove(
            "done"
        );

    }

}


function setProcessingRunning(
    stepNumber
) {

    const step =
        document.getElementById(
            "process" + stepNumber
        );


    if (step) {

        step.classList.add(
            "running"
        );

    }

}


function markProcessingDone(
    stepNumber
) {

    const step =
        document.getElementById(
            "process" + stepNumber
        );


    if (step) {

        step.classList.remove(
            "running"
        );


        step.classList.add(
            "done"
        );

    }

}


/* =========================================================
   NORMALIZE TYPE
========================================================= */

function normalizeQuestionType(
    type
) {

    if (!type) {

        return "Theory";

    }


    const value =
        type
            .toString()
            .trim()
            .toLowerCase();


    if (
        value.includes("numerical") ||
        value.includes("math")
    ) {

        return "Numerical";

    }


    if (
        value.includes("diagram")
    ) {

        return "Diagram";

    }


    if (
        value.includes("mixed")
    ) {

        return "Mixed";

    }


    return "Theory";

}


/* =========================================================
   ESCAPE HTML
========================================================= */

function escapeHtml(
    value
) {

    const div =
        document.createElement(
            "div"
        );


    div.textContent =
        value ?? "";


    return div.innerHTML;

}


/* =========================================================
   SLEEP
========================================================= */

function sleep(
    milliseconds
) {

    return new Promise(
        resolve =>
            setTimeout(
                resolve,
                milliseconds
            )
    );

}
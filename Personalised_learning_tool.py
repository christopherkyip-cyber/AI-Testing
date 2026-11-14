import warnings
warnings.filterwarnings("ignore")
import logging
logging.getLogger("google_genai").setLevel(logging.ERROR)
logging.getLogger("google.genai").setLevel(logging.ERROR)
import gradio as gr
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_neo4j import GraphCypherQAChain, Neo4jGraph
from langchain_core.prompts import PromptTemplate
from neo4j import GraphDatabase
import random
import time
import threading

css = """
.gradio-container {
    padding-left: 5%;
    padding-right: 5%;
    padding-top: 3%;
    padding-bottom: 3%;
}
"""

#module tracking
next_modules = "Information Feedback Module"
quiz_questions_answered = 0
quiz_answered_correctly_streak = 0
quiz_questions_answered_correctly = 0
total_quiz_questions = 3
score_multiplier = 1

#Question goes to GraphCypherQAChain and Langchain, which gives the gemini the schema and question.
#Gemeni with the instuctions generate a Cypher query, which is then sent to the Neo4j database. 
#It is then reutrned to gemeni and gemeni provides a human friendly answer to the user.

#Neo4j initialisation
#https://www.youtube.com/watch?v=EQbY6d2hl5Y&t=1602s
graph = Neo4jGraph(
    url= "<INSERT_YOUR_NEO4J_URL_HERE>",
    username="<INSERT_YOUR_NEO4J_USERNAME_HERE>",
    password="<INSERT_YOUR_NEO4J_PASSWORD_HERE>",
    database="<INSERT_YOUR_NEO4J_USERNAME_HERE>",
)

#https://www.youtube.com/watch?v=EkKSCuTHrLk
driver = GraphDatabase.driver(
    "<INSERT_YOUR_NEO4J_URL_HERE>",
    auth=("<INSERT_YOUR_NEO4J_USERNAME_HERE>", "<INSERT_YOUR_NEO4J_PASSWORD_HERE>")
)

driver.verify_connectivity()

#Gemeni initialisation
#https://docs.langchain.com/oss/python/integrations/llms/google_generative_ai
cypher_llm = ChatGoogleGenerativeAI(
    model="<INSERT_YOUR_SELECTED_GEMENI_MODEL_NAME_HERE>",
    google_api_key="<INSERT_YOUR_MODEL_API_KEY_HERE>"
)

qa_llm = ChatGoogleGenerativeAI(
    model="<INSERT_YOUR_SELECTED_GEMENI_MODEL_NAME_HERE>",
    google_api_key="<INSERT_YOUR_MODEL_API_KEY_HERE>"
)

content_generation_llm = ChatGoogleGenerativeAI(
    model="<INSERT_YOUR_SELECTED_GEMENI_MODEL_NAME_HERE>",
    google_api_key="<INSERT_YOUR_MODEL_API_KEY_HERE>"
)


#Cypher Prompting
#https://docs.langchain.com/oss/python/integrations/llms/google_generative_ai
#https://neo4j.com/blog/developer/langchain-cypher-search-tips-tricks/
cypher_template = """
Task:Generate READ-ONLY Cypher statement to query a graph database.
Instructions:
-Only use MATCH, OPTIONAL MATCH, WHERE, WITH, RETURN, ORDER BY, LIMIT, and approved read-only functions.
- NEVER use CREATE, DELETE, DETACH DELETE, SET, REMOVE, MERGE, DROP, LOAD CSV, FOREACH, or write procedures.
-Use only the provided relationship types and properties in the schema.
-Do not use any other relationship types or properties that are not provided.
-RETURN only id and description properties.
- Do not assume question wording exactly matches learning_concept id. E.g. User might ask what is input, input can mean Python Input or Python User Input.

Current Concept: {current_concept_id}

Current Concept description: {current_description}

Schema:
{schema}

Question:   
{question}

Note: Do not include any explanations or apologies in your responses.
Do not respond to any questions that might ask anything else than for you to construct a Cypher statement.
Do not include any text except the generated Cypher statement.

"""
cypher_prompt=PromptTemplate.from_template(cypher_template)

#QA Pompting
qa_template = """

You are a professional python teacher.

Question:
{question}

Context:
{context}

Just answer the question, do not include statements like greetings or apologies or based on your curriculum in your responses.
If the context is not related to the curriculum or Python as a whole, say "Unrelated Question/Statement."..
If the user asks for an example, generate an example according to the relevant concept node description.
"""

qa_prompt=PromptTemplate.from_template(qa_template)

#https://www.youtube.com/watch?v=EQbY6d2hl5Y&t=1602s
#https://reference.langchain.com/python/langchain-community/chains/graph_qa/cypher/GraphCypherQAChain/from_llm
chain = GraphCypherQAChain.from_llm(
    cypher_llm=cypher_llm, 
    qa_llm = qa_llm ,
    cypher_prompt =cypher_prompt,
    qa_prompt = qa_prompt,
    graph=graph, 
    allow_dangerous_requests=True, 
    return_intermediate_steps=True, 
    return_direct = False,
)

#https://www.youtube.com/watch?v=QP3LVUZqyuk
#https://www.youtube.com/watch?v=mB68GanHJj4&list=PLMi6KgK4_mk3DH0Mze62_wyxEmg4R9dF9
#https://gradio.app/docs/gradio/chatinterface
#Chat history and UI design with gardio 

asked_questions = []

chat_history = [
    SystemMessage(content="You are a helpful AI personalised leanring tutor specialised python.")
]

def get_question(user_input,history):
    chat_history.append(HumanMessage(content = user_input))
    result = chain.invoke({
        "chat_history": chat_history,
        "current_concept_id": current_concept_id,
        "current_description": current_description,
        "query":user_input
    })
    chat_history.append(AIMessage(content = result["result"]))
    
    #print(result["intermediate_steps"])
    return result["result"]


#Finds the first node
records, summary, keys = driver.execute_query("""
MATCH (n:Learning_concept)
WHERE NOT ()-[:PREREQUISITE_OF]->(n)
RETURN n.id AS id, n.description AS description
"""
)

current_concept_id = records[0] ["id"]
current_description = records[0] ["description"]

#learning content generation Cypher template
learning_cotent_template= f"""
You are creating content for a personalised learning application.
Create Learning content that is ONLY concept related, concept description will also be supplied below.
Just generate the contet, do not include apologies, or expressions, only return learning content material.
Keep it factual, and easy to understand, these are beginner learners.
Include exmaples as well, at least one, no more than 3.

Concept name: 
{current_concept_id}

Concept Description:
{current_description}
"""

#The below code generates the learning materials of the first concept
learning_generated_content = content_generation_llm.invoke(learning_cotent_template)
content_text = learning_generated_content.content[0]["text"]

learned_concepts = {}
p_init = 0.25
p_learn = 0.15
p_guess = 0.20
p_slip = 0.10

learned_concepts[current_concept_id] = p_init

completion_times = []

expected_time = 0



def educational_module():
    global current_concept_id,current_description,learned_concepts, learning_content_preference

    if(learned_concepts[current_concept_id] > 0.8):

        records, summary, keys = driver.execute_query("""
            MATCH (Learning_concept{id: $current_concept_id})
            -[:PREREQUISITE_OF]->(
            next:Learning_concept)
                                                        
            RETURN next.id AS id, next.description AS description
            """,
            parameters_={
                "current_concept_id": current_concept_id
            }
        )

        if records:

            current_concept_id= records[0]["id"]
            current_description = records[0]["description"]
            learned_concepts[current_concept_id] = p_init
        

    elif(learned_concepts[current_concept_id] < 0.5):

        records, summary, keys = driver.execute_query("""
            MATCH (Learning_concept{id: $current_concept_id})
            <-[:PREREQUISITE_OF]-(
            previous:Learning_concept)
                                                        
            RETURN previous.id AS id, previous.description AS description
            """,
            parameters_={
                "current_concept_id": current_concept_id
            }
        )

        if records:

            current_concept_id= records[0]["id"]
            current_description = records[0]["description"]
            learned_concepts[current_concept_id] = p_init
        

    learning_cotent_template= f"""
    Include the Concept name in the first line, then new line.
    You are creating content for a personalised learning application.
    Create Learning content that is ONLY concept related, concept description will also be supplied below.
    Just generate the content, do not include apologies, or expressions, only return learning content material.
    Keep it factual, and easy to understand, these are beginner learners.
    Include exmaples as well, at least one, no more than 3
    Try your best to incporate the student's Educational Content Preference

    Concept name: 
    {current_concept_id}

    Concept Description:
    {current_description}

    Educational Content Preference
    {learning_content_preference}
    """

    learning_generated_content = content_generation_llm.invoke(learning_cotent_template)
    content_text = learning_generated_content.content[0]["text"]

    return content_text

choices = ["A","B","C","D","E"]
    


def information_feedback_module():
    global expected_time,current_concept_id,current_description

    history = "\n".join(asked_questions)

    question_type = random.choice(["mcq","coding"])

    if question_type == "mcq":

        #get mcq answer randomly
        number = random.randint(0,4)
        answer = choices[number]

        expected_time = 30

        feedback_module_template= f"""
        You are operating the quiz section of a personalised learning application.
        Create one beginner Python Conceptual question based only on the current learning concept and concept description supplied below. 
        
        General Rules:
        - Only test knowledge related to the current concept description.
        -The learner CANNOT read the concept description you are seeing, ask general questions surrounding the description.
        -Keep the question factual, clear and suitable for python beginners.
        -Generate only One question.
        -The question must have one clearly correct answer.
        -NEVER generate coding related question.
        -NEVER ask the learner to determine what a piece o code outputs.
        -NEVER ask the learner to write, complete, fix or interpret code.
        -You MUST ask only theoretical and conceptual questions about the current concept and desciption listed below.
        -Do not provide any sort of hints or explanation.
        -Use multiple lines when listing options.
        -Do not reveal the anser before ###ANSWER###.
        -Always write ###ANSWER### on it's own line.
        -After ###ANSWER###, provide only the correct answer.
        -Do not include any other text after ###ANSWER###.
        -Do NOT repeat any of these questions:
        {history}
    

        Multiple Choice Question Rules:
        - Only ask conceptual and theoretical questions, do not ask any sort of Coding question.
        - Provide exactly 5 options A, B, C, D, E.
        - Only one option may be correct.
        -The correct answer must be {answer}
        -After ###ANSWER### provide exactly the letter {answer}
        -Do not MOVE the correct answer to another letter

        FORBIDDEN example:
        "What is the exact output of the following python code?"
        This style of question must NEVER be generated.


        Example:

        Which symbol is used to asign a value to a variable in Python?

        A. ==

        B. =

        C. !=

        D. >=

        E. >
        
        ###ANSWER###
        B

        Concept name: 
        {current_concept_id}

        Concept Description:
        {current_description}

       """
    else:

        expected_time = 60
        feedback_module_template= f"""
        You are operating the quiz section of a personalised learning application.
        Create one beginner Python Coding question based only on the current learning concept and concept description supplied below. 

        
        General Rules:
        - Only test knowledge related to the current concept description.
        -The learner CANNOT read the concept description you are seeing, ask general questions surrounding the description.
        -Keep the question factual, clear and suitable for python beginners.
        -Generate only One question.
        -No multiple choice
        -The question must have one clearly correct answer.
        -Do not provide any sort of hints or explanation.
        -Use multiple lines when listing options.
        -Do not reveal the anser before ###ANSWER###.
        -Always write ###ANSWER### on it's own line.
        -After ###ANSWER###, provide only the correct answer.
        -Do not include any other text after ###ANSWER###.
        -Do NOT repeat any of these questions:
        {history}
    
        Coding Output Question Rules:
        - Ask the learner to determine the exact output of a short Python Program.
        - The python code must be valid and syntactically correct.
        - Keep the code appropriate for beginners.
        - Preserve all necessary line breaks in Python Code.
        - After ###ANSWER### provide ONLY the exact output produced by the code.
        - Do not repeat the Python Code in the answer.
        - Preserve line breaks, spaces, puntuations, numbers, capitalisations of the Python output.
        - Each print() produces a new line.

        Example

        ```python
        name = Chris
        print("Hello")
        print(name)```

        ###ANSWER###
        Hello
        Chris


        Concept name: 
        {current_concept_id}

        Concept Description:
        {current_description}
        """

    feedback_module_content = content_generation_llm.invoke(feedback_module_template)
    feedback_content_text = feedback_module_content.content[0]["text"]

    return feedback_content_text

def next_module():
    global completion_times,start_time, next_modules,quiz_questions_answered,quiz_questions_answered_correctly, total_quiz_questions,score_multiplier
    
    if len(completion_times) != 0:
        average_time_past_6_questions = sum(completion_times)/len(completion_times)

    if quiz_answered_correctly_streak >= 6 and average_time_past_6_questions < 0.5 :
        total_quiz_questions = 1
        score_multiplier = 3
    else:
        total_quiz_questions = 3
        score_multiplier = 1
    
    #answer,next,submit
    if (next_modules == "Information Feedback Module"):
        next_modules = "Educational Module"

        quiz_questions_answered = 0
        quiz_questions_answered_correctly = 0

        question, correct_answer = question_and_answer_generation()

        start_time = time.perf_counter()

        return (question,correct_answer,gr.update(visible=True),gr.update(visible=False),gr.update(visible=True),gr.update(visible= False),gr.update(visible= False))
    
    elif(next_modules == "Educational Module"):
        next_modules = "Information Feedback Module"

        return (educational_module(),"null",gr.update(visible= False),gr.update(visible=True),gr.update(visible=False),gr.update(visible= False),gr.update(visible= True))

def question_and_answer_generation():

    global asked_questions

    text = information_feedback_module()

    question, answer = text.split("###ANSWER###")
    question = question.strip()
    correct_answer = answer.strip()

    asked_questions.append(question)

    return question, correct_answer

def revist_module():
    global current_concept_id,current_description, next_modules

    next_modules = "Information Feedback Module"

    learning_cotent_template= f"""
    You are creating content for a personalised learning application.
    Create Learning content that is ONLY concept related, concept description will also be supplied below.
    Just generate the content, do not include apologies, or expressions, only return learning content material.
    Keep it factual, and easy to understand, these are beginner learners.
    Include exmaples as well, at least one, no more than 3

    Concept name: 
    {current_concept_id}

    Concept Description:
    {current_description}
    """

    learning_generated_content = content_generation_llm.invoke(learning_cotent_template)
    content_text = learning_generated_content.content[0]["text"]

    return (content_text,gr.update(visible= True),gr.update(visible= False),gr.update(visible= False),gr.update(visible= False))

def add_time(newTime):
    completion_times.append(newTime)

    if(len(completion_times) >6):
        completion_times.pop(0)

def checking_answer(student_answer, correct_answer):


    global expected_time,completion_times,start_time,score_multiplier, quiz_answered_correctly_streak,learned_concepts,current_concept_id,quiz_questions_answered, quiz_questions_answered_correctly,total_quiz_questions,p_init,p_guess,p_learn,p_slip
    
    end_time = time.perf_counter()
    time_taken = round(end_time - start_time,2)
    NRT = time_taken/expected_time
    add_time(NRT)



    if student_answer == correct_answer:
        quiz_questions_answered_correctly = quiz_questions_answered_correctly + 1

        for i in range(score_multiplier):
            masteryScore = learned_concepts[current_concept_id]
            newMasteryScore = (masteryScore * (1-p_slip))/(masteryScore*(1-p_slip)+(1-masteryScore)*p_guess)
            updatedNewMasteryScore = (newMasteryScore + (1- newMasteryScore)*p_learn)
            learned_concepts[current_concept_id] = round(updatedNewMasteryScore,2)

        quiz_answered_correctly_streak = quiz_answered_correctly_streak + 1

    else:
        masteryScore = learned_concepts[current_concept_id]
        newMasteryScore = (masteryScore*p_slip)/(masteryScore*p_slip+(1-masteryScore)*(1-p_guess))
        updatedNewMasteryScore = (newMasteryScore + (1- newMasteryScore)*p_learn)
        learned_concepts[current_concept_id] = round(updatedNewMasteryScore,2)
        quiz_answered_correctly_streak = 0

    quiz_questions_answered = quiz_questions_answered + 1

    if quiz_questions_answered < total_quiz_questions:

        question, new_correct_answer = question_and_answer_generation()

        start_time = time.perf_counter()

        return (question,new_correct_answer,gr.update(visible= False),gr.update(visible= True),gr.update(visible= False),gr.update(visible= True))


    else:
        output = f""" You got {quiz_questions_answered_correctly} right"""

        return (output,"null",gr.update(visible= True),gr.update(visible= False),gr.update(visible= False),gr.update(visible= False))

    



def begin_learning():
    shutdown_timer.start()
    return (gr.Row(visible= True),gr.Column(visible=False))



def update_concepts():
    text = "Concepts Learned (Mastery Score)\n"
    for concept, score in learned_concepts.items():
        text += f"- {concept}: {score} \n"

    return text

learning_content_preference = ""

def store_choice(selected_prefernce):
    global learning_content_preference

    learning_content_preference =selected_prefernce

def shutdown_app():
    print("Evaluation Done")
    print(learned_concepts)
    interface.close()

shutdown_timer = threading.Timer(
    60*30,
    shutdown_app
)

with gr.Blocks(css = css) as interface:

    with gr.Column(visible = True) as welcome:
        gr.Markdown("""
                    
                    The pesonalised learning program consists of learning modules and quiz modules, you'll be resticted access and receive an error after 30 minutes.
                    \n

                    The educational modules' primary objective is to educate students on certain concepts, where the student either reaches the next level of knowledge or revisits a concept, influenced by the mastery score on the left. Educational modules consist of LLM-generated teaching content, which is where the student mainly does most of the learning. The module also includes an AI learning assistant on the right, feel free to ask it any questions,  you can adjust the learning style as well.
                    \n

                    The quiz module is consists of mini-quizzes to grasp students' current understanding. the mastery score score will increase when answered correctly and vice versa. Only quiz modules changes your mastery score. (Less than 0.5 you go down, 0.5-0.8 you stay and 0.8 and above you progress)

                    \n

                    If you answer 6 questions in a row correctly in rapid succession, you will only be asked 1 question per concept instead of the default 3. So try to balance your answering speed alongside accuracy.

                    \n
                    \n

                    ## I will be asking the following questions after the completeion of evaluation, please keep them in mind when completeing the learning course, thanks.

                    \n

                    Did the Personalised Learning System make concepts easier to understand?

                    \n

                    Was the system straightforward to use?

                    \n

                    Did you enjoy learning with the system?

                    \n

                    Were you satisfied with your final result?

                    \n

                    Would you use similar systems to learn in the future?

                    \n

                    Are you confident in your newly learned programming abilities?

                    
                    """)
        preference  = gr.Dropdown(
            choices = ["Worked examples", "Detailed Explanation", "Step-by-Step guidance", "Short and concise explanations","Utilise Analogies"],
            label = "Prefered Learning Style"
        )

        begin_button = gr.Button("Start learning!")

    

    with gr.Row(visible = False) as learning:
        with gr.Column(scale=2):
            concept_list = gr.Markdown("## **Concepts Learned**")

        with gr.Column(scale=6):
            gr.Markdown("## **Learning Content**")
            content_output = gr.Markdown(content_text)

            answer_box = gr.Textbox(
                label="Answer here (Answer has to be exact, so double check. For Multiple Choice Questions, please type the selected letter in capitol. To type a new line, do shift + Enter on the keyboard)",
                visible=False,
                interactive=True
            )

            correct_answer_state = gr.State(
                ""
            )

            next_button = gr.Button(
                "Next Lesson",
                visible = True
            )

            revisit_lesson_button = gr.Button(
                "Revisit Lesson",
                visible = False
            )

            submit_button = gr.Button(
                "Submit Answer",
                visible = False
            )

            revisit_lesson_button.click(
                fn = revist_module,
                outputs = [content_output,next_button,submit_button,revisit_lesson_button,answer_box]
            )

            submit_button.click(
                fn = checking_answer,
                inputs =[answer_box,correct_answer_state],
                outputs = [content_output,correct_answer_state,next_button,submit_button,revisit_lesson_button,answer_box]
            ).then(
                fn = update_concepts,
                outputs = concept_list
            )


        begin_button.click(
        fn=begin_learning,
        outputs=[learning,welcome]
        )

        preference.change(
            fn = store_choice,
            inputs = [preference]
        )
        
        with gr.Column(scale=2) as tutor_and_pref:
            gr.Markdown("## **AI Tutor**")
            gr.ChatInterface(
                fn=get_question
            )
            preference  = gr.Dropdown(
            choices = ["More worked examples", "More Detailed Explanation", "Step-by-Step guidance", "Short and concise explanations", "Utilise Analogies"],
            label = "Prefered Learning Style"
            )

        preference.change(
                fn = store_choice,
                inputs = [preference]
        )

        next_button.click(
            fn = next_module,
            outputs=[content_output,correct_answer_state, answer_box, next_button, submit_button,revisit_lesson_button,tutor_and_pref]
        )

interface.launch(share=True)
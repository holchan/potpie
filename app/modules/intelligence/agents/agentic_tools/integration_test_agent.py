import os
from typing import Any, Dict, List

from crewai import Agent, Crew, Process, Task
from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.modules.conversations.message.message_schema import NodeContext
from app.modules.intelligence.agents.agentic_tools.test_plan_agent import TestPlanAgent
from app.modules.intelligence.tools.code_query_tools.get_code_graph_from_node_id_tool import (
    GetCodeGraphFromNodeIdTool,
)
from app.modules.intelligence.tools.kg_based_tools.get_code_from_node_id_tool import (
    get_code_tools,
)


class IntegrationTestAgent:
    def __init__(self, sql_db, llm):
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.sql_db = sql_db
        self.code_tools = get_code_tools(self.sql_db)
        self.test_plan_agent = TestPlanAgent(sql_db, llm)
        self.llm = llm
        self.max_iterations = os.getenv("MAX_ITER", 15)

    async def create_agents(self):
        integration_test_agent = Agent(
            role="Integration Test Writer",
            goal="Create a comprehensive integration test suite for the provided codebase. Analyze the code, determine the appropriate testing language and framework, and write tests that cover all major integration points.",
            backstory="You are an expert in writing unit tests for code using latest features of the popular testing libraries for the given programming language.",
            allow_delegation=False,
            verbose=True,
            llm=self.llm,
        )

        return integration_test_agent

    class TestAgentResponse(BaseModel):
        response: str = Field(
            ...,
            description="String response containing the test plan and the test suite",
        )
        citations: List[str] = Field(
            ..., description="Exhaustive List of file names referenced in the response"
        )

    async def create_tasks(
        self,
        node_ids: List[NodeContext],
        project_id: str,
        query: str,
        graph: Dict[str, Any],
        history: List[str],
        integration_test_agent,
    ):
        node_ids_list = [node.node_id for node in node_ids]

        integration_test_task = Task(
            description=f"""Your mission is to create comprehensive test plans and corresponding integration tests based on the user's query and provided code.

            **Process:**

            1. **Code Graph Analysis:**
            - Code structure is defined in the {graph}
            - **Graph Structure:**
                - Analyze the provided graph structure to understand the entire code flow and component interactions.
                - Identify all major components, their dependencies, and interaction points.
            - **Code Retrieval:**
                - Fetch the docstrings and code for the provided node IDs using the `get_code_from_node_id` tool.
                - Node IDs: {', '.join(node_ids_list)}
                - Project ID: {project_id}
                - Fetch the code for all relevant nodes in the graph to understand the full context of the codebase.
            
            2. **Detailed Component Analysis:**
            - **Functionality Understanding:**
                - For each component identified in the graph, analyze its purpose, inputs, outputs, and potential side effects.
                - Understand how each component interacts with others within the system.
            - **Import Resolution:**
                - Determine the necessary imports for each component by analyzing the graph structure.
                - Use the `get_code_from_probable_node_name` tool to fetch code snippets for accurate import statements.
                - Validate that the fetched code matches the expected component names and discard any mismatches.

            3. **Test Plan Generation:**
            - **Comprehensive Coverage:**
                - For each component and their interactions, create detailed test plans covering:
                - **Happy Path Scenarios:** Typical use cases where interactions work as expected.
                - **Edge Cases:** Scenarios such as empty inputs, maximum values, type mismatches, etc.
                - **Error Handling:** Cases where components should handle errors gracefully.
                - **Performance Considerations:** Any relevant performance or security aspects that should be tested.
            - **Integration Points:**
                - Identify all major integration points between components that require testing to ensure seamless interactions.

            4. **Integration Test Writing:**
            - **Test Suite Development:**
                - Based on the generated test plans, write comprehensive integration tests that cover all identified scenarios and integration points.
                - Ensure that the tests include:
                - **Setup and Teardown Procedures:** Proper initialization and cleanup for each test to maintain isolation.
                - **Mocking External Dependencies:** Use mocks or stubs for external services and dependencies to isolate the components under test.
                - **Accurate Imports:** Utilize the analyzed graph structure to include correct import statements for all components involved in the tests.
                - **Descriptive Test Names:** Clear and descriptive names that explain the scenario being tested.
                - **Assertions:** Appropriate assertions to validate expected outcomes.
                - **Comments:** Explanatory comments for complex test logic or setup.

            5. **Reflection and Iteration:**
            - **Review and Refinement:**
                - Review the test plans and integration tests to ensure comprehensive coverage and correctness.
                - Make refinements as necessary, respecting the max iterations limit of {self.max_iterations}.

            6. **Response Construction:**
            - **Structured Output:**
                - Provide the test plans and integration tests in your response.
                - Ensure that the response is JSON serializable and follows the specified Pydantic model.
                - For citations, include only the `file_path` of the nodes fetched and used.

            **Constraints:**
            - **User Query:** Refer to the user's query: "{query}"
            - **Chat History:** Consider the chat history: '{history[-min(5, len(history)):]}' for any specific instructions or context.
            - **Iteration Limit:** Respect the max iterations limit of {self.max_iterations} when planning and executing tools.

            **Output Requirements:**
            - Ensure that your final response is JSON serializable and follows the structure outlined in the Pydantic model: {self.TestAgentResponse.model_json_schema()}
            - Do not wrap the response in ```json, ```python, ```code, or ``` symbols.
            - For citations, include only the `file_path` of the nodes fetched and used.
            """,            
            expected_output=f"Write COMPLETE CODE for integration tests for each node based on the test plan. Ensure that your output ALWAYS follows the structure outlined in the following pydantic model:\n{self.TestAgentResponse.model_json_schema()}",
            agent=integration_test_agent,
            output_pydantic=self.TestAgentResponse,
            tools=[self.code_tools[2], self.code_tools[0]],
        )

        return integration_test_task

    async def run(
        self,
        project_id: str,
        node_ids: List[NodeContext],
        query: str,
        graph: Dict[str, Any],
        history: List,
    ) -> Dict[str, str]:
        os.environ["OPENAI_API_KEY"] = self.openai_api_key

        integration_test_agent = await self.create_agents()
        integration_test_task = await self.create_tasks(
            node_ids,
            project_id,
            query,
            graph,
            history,
            integration_test_agent,
        )

        crew = Crew(
            agents=[integration_test_agent],
            tasks=[integration_test_task],
            process=Process.sequential,
            verbose=True,
        )

        result = await crew.kickoff_async()
        return result


async def kickoff_integration_test_crew(
    query: str,
    project_id: str,
    node_ids: List[NodeContext],
    sql_db,
    llm,
    history: List[str],
) -> Dict[str, str]:
    if not node_ids:
        raise HTTPException(status_code=400, detail="No node IDs provided")
    graph = GetCodeGraphFromNodeIdTool(sql_db).run(project_id, node_ids[0].node_id)

    def extract_node_ids(node):
        node_ids = []
        for child in node.get("children", []):
            node_ids.extend(extract_node_ids(child))
        return node_ids

    def extract_unique_node_contexts(node, visited=None):
        if visited is None:
            visited = set()
        node_contexts = []
        if node["id"] not in visited:
            visited.add(node["id"])
            node_contexts.append(
                NodeContext(node_id=node["id"], name=node["name"])
            )  
            for child in node.get("children", []):
                node_contexts.extend(extract_unique_node_contexts(child, visited))
        return node_contexts

    node_contexts = extract_unique_node_contexts(graph["graph"]["root_node"])
    integration_test_agent = IntegrationTestAgent(sql_db, llm)
    result = await integration_test_agent.run(
        project_id, node_contexts, query, graph, history
    )
    return result
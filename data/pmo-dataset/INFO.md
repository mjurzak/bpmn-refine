# PMo Dataset

The PMo Dataset is a collection of 55 process models and their corresponding textual descriptions, designed to facilitate research in Process Modeling. 


## Data sources

The dataset combines and standardizes 4 existing sets:

### PMo Benchmark (Kourani et al., 2024): Pairs 01 to 20
- Same names: 01 to 20 -> 01 to 20

### BPMN for research (Camunda, 2015): Pairs 21 to 24
- 01-Dispatch-of-goods -> 21
- 02-Recourse -> 22
- 03-Credit-scoring -> 23
- 04-Self-service-restaurant -> 24

### Textual Process Descriptions and Corresponding BPMN Models (Mangler et al., 2023): Pairs 25 to 48 
- E_j01 -> 25
- E_j02 -> 26
- E_j03 -> 27
- E_j04 -> 28
- E_j05 -> 29
- G_g01 -> 30
- G_g03 -> 31
- G_j01 -> 32
- M_g01 -> 33
- M_g02 -> 34
- M_j01 -> 35
- M_j02 -> 36
- M_j03 -> 37
- M_k05 -> 38
- R_g01 -> 39
- R_j01 -> 40
- R_j02 -> 41
- R_j03 -> 42
- R_j04 -> 43
- V_g01 -> 44
- V_k08 -> 45
- V_k09 -> 46
- X_g01 -> 47
- X_g03 -> 48

### PET-7 (Klievtsova et al., 2024): Pairs 49 to 54
- 1-2 -> 49
- 1-3 is removed because description is a duplicate with description 20 from PMo benchmark
- 3-3 -> 50
- 5-2 -> 51
- 10-1 -> 52
- 10-6 -> 53
- 10-13 -> 54

### CCC19 (Munoz-Gama et al., 2019): Pair 55


## Content

Textual descriptions are in the `descriptions` folder. Each sentence is a separate line. Some descriptions include a title.

Each process model is represented in 9 different Process Model Representations (PMRs):
- **BPMN**: standardized BPMN representation containing tasks, events, gateways, sequence flows, swimlanes and message flows. All other representations are derived from this standardized BPMN representation. **This representation should be used as the ground truth for the process model.**
- **BPMN process**: BPMN without the diagram definition. 
- **BPMN text**: branching XML representation from "MAO: A Framework for Process Model Generation with Multi-Agent Orchestration" (Lin et al., 2024). It has the same limitations as *JSON branches* except that it supports role and object annotations in tasks.
- **Graphviz**: Can be directly visualized using the DOT engine. Special attention has been given to the look of the visualized process model. The graph is structured from left to right, with tasks represented as rectangles and events as circles with names as labels. End events are depicted with a bolded outer circle, while intermediate events use a double circle, consistent with BPMN. Gateways are represented as follows: Exclusive (X or condition), Parallel (+), Event-based (E), Inclusive (O), and Complex (*). Swimlanes are represented as nested boxes.
- **JSON branches**: Introduced in "Efficient LLM-Based Conversational Process Modeling" (Kopke and Safan, 2024) specifically to reduce token count, be compact and provide schema following abilities for LLM generation. It only handles simple backward flows (represented as looping gateways) and cannot include multiple start and end events, message flows and swimlanes.
- **Mermaid**: Follow the same rules as Graphviz, with the main difference being that styling properties (e.g., shape) are defined within the flow definitions rather than at the end of the process definition.
- **Process Model Elements (PME)**: Introduced in "Generative AI for Business Process Management - Suitability of Modalities" (Volter et al., 2024). It consists of a JSON object containing 6 lists of elements: tasks, events, gateways, swimlanes, sequence flows and message flows. This flat structure enables easy counting of elements and comparison between models.
- **POWL code**: Introduced by Kourani et al. (2024) ("ProMoAI: Process Modeling with Generative AI"), is Python code that can be executed to create a POWL model, itself translatable to BPMN. The main advantages of using code as a PMR is to leverage LLM's familiarity with coding tasks while enabling detailed feedback via execution of said code. POWL code lacks support for conditions, event labels, intermediate events, swimlanes and message flows, making it the least expressive PMR.
- **Simplified XML**: Simplification of XML originally proposed in "Leveraging Large Language Models for Enhanced Process Model Comprehension" (Kourani et al., 2024). Similarly to BPMN process, graphical and other non-essential elements are omitted. The XML structure is lightened, resulting in a more compact PMR.

### Variations

Additional PMR variations are included:
- **BPMN original**: BPMN file without standardization (i.e., containing additional elements such as text annotations, tool specific elements, etc.). The process diagram includes the process description as a text annotation for quick visualization.
- **POWL code original**: Original POWL code from PMo Benchmark (Kourani et al., 2024). Only for pairs 01 to 20.
- **Mermaid/Graphviz with IDs**: Alternative notation of Mermaid/Graphviz based on element IDs. Less compact but may be easier to understand for humans/LLMs.
- **Basic versions**: Basic versions only contain standard tasks, start and end events, exclusive and parallel gateways, and sequence flows. They're simplified versions of the process models, initially created for Process Model Generation experiments.


## Dataset creation

These data sources are chosen because process models are handcrafted, or at least validated by domain experts. This explains why we do not include the much larger MaD dataset (Li et al., 2023), which has been criticized for lack of variability of its models and their descriptions. Even though the descriptions are human-authored, the dataset from "Local Large Language Models for Business Process Modeling" (Apaydin et al., 2025) also lacks diversity due to automatic process model generation (e.g., maximum 9 activities).

The PMo Dataset underwent extensive preprocessing to ensure optimal usability and applicability. For process descriptions, this involves cleaning special characters, correcting punctuation and spacing, sentence splitting, and removing irrelevant information such as modeling instructions. The ground truth BPMN models are also refined by sanitizing label texts, improving diagram layouts and positioning decisions and conditions optimally.
The Mangler dataset received special attention due to having multiple models per description, each graded from 0 to 5. The model closest to the textual description (to our own judgment) is chosen among those with the best grade, with a preference for models including only common elements (e.g., no data objects or other special elements).

Following the preprocessing phase, we automatically convert all BPMN models into all the other representations to obtain our ground truth. We develop converters for each PMR and validate our conversions by transforming models from BPMN to PMRs and back.
Some process models contain information that are not supported by every PMR. In this case, we ignore the additional information (e.g., conditions are not included in POWL code). In cases where the model cannot be represented by the PMR without significant loss of information, it is left out (e.g., models including swimlanes are not converted to JSON branches).

Original code used to generate the dataset can be found here: https://github.com/Lama-West/Process_Model_Representations.

### Known limitations

Contributions are welcomed to mitigate the following limitations:

- All conversions use PME as an intermediate representation. This is intended as BPMN is more complex to parse. 
- The conversion to JSON branches is not optimal, resulting in less process models that can be represented in this PMR. Improving the gateway matching could improve the process model coverage of this PMR.
- BPMN text and POWL code conversions use JSON branches as an intermediate step, resulting in the same limitations. 
- Graphviz, Mermaid, Simplified XML, JSON branches and BPMN text only support conversion back to BPMN in their *basic* versions. Adding support for swimlanes and other complex elements would be a nice improvement.
- POWL code use a custom logic for the conversion from BPMN, using the original code developed by Kourani et al. (https://github.com/humam-kourani/ProMoAI) would provide better conversions. 


## Citation

Please cite the following paper if you use this dataset:

Alexis Brissard, Frédéric Cuppens, and Amal Zouaq. *What is the Best Process Model Representation? A Comparative Analysis for Process Modeling with Large Language Models.* Accepted to AI4BPM Workshop at BPM 2025.

```bibtex
@inproceedings{brissard2025pmr,
  title     = {What is the Best Process Model Representation? A Comparative Analysis for Process Modeling with Large Language Models},
  author    = {Alexis Brissard and Frédéric Cuppens and Amal Zouaq},
  booktitle = {Proceedings of the AI4BPM Workshop at BPM 2025},
  year      = {2025},
  note      = {Accepted for publication}
}
```

This dataset was released as a companion artifact to the above paper.
Once it is published, this README will be updated with a full citation and DOI.


## Version

This is version 1.0.0 of the PMo Dataset (July 2025).
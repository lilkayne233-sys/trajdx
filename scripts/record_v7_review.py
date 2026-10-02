#!/usr/bin/env python
"""Persist the v7 round's AI review verdicts for the three review batches.

Decisions are enumerated by reviewed packet number, not inferred from outcome
labels or copied from detector output.  Each entry is
``(review_dir, detector, old_packet_no, verdict, reason, evidence_steps)``.

Packet numbering note: re-running prepare_review after the ``no_submit``
exit_status fix renumbered the 70B/405B termination packets (31 no_submit
findings disappeared as stale).  Those verdicts are therefore joined through
``old_termination_mapping.json`` (old packet -> instance_id + pattern); every
other detector's numbering was unchanged by the fix.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path("data/reports")

V = "valid"
I = "invalid"
U = "uncertain"

# (review_dir, detector, old_packet_no, verdict, reason, evidence_steps)
DECISIONS = [
    # ---- 70B verification_gap (fresh round: keyed by stable packet number) ----
    *[( "review_v7_70b", "verification_gap", n, v, r, s) for n, v, r, s in [
        (1, I, "步骤8在编辑后重跑了lexicon memset CLI探针，输出中出现'Auth token: None'，说明编辑后的代码已被实际执行验证（403是环境无凭证所致），验证以检测器不认识的CLI形式跑过", [7,8]),
        (2, V, "步骤38对checker.py的Redefinition逻辑做了实质性重写（新增ast.FunctionDef分支），而最后一次flake8验证在步骤36，该修改提交前从未被验证", [36,38]),
        (3, V, "步骤2编辑conjunctive_graphs.py后步骤3直接submit，全程无任何测试或脚本执行", [2,3]),
        (4, I, "步骤6、8在编辑profiling.py后重跑了asv profile CLI探针来检验修改，验证以检测器不识别的CLI形式执行过", [5,6,7,8]),
        (5, V, "对config_parser.py的三次编辑（步骤3-5）后直接submit，全程无任何测试或探针运行", [3,4,5,6]),
        (6, I, "步骤7、11在编辑cli.py后重跑pypistats python_minor CLI，步骤11输出正常表格，说明修改已通过CLI探针实际验证", [6,7,10,11]),
        (7, V, "步骤7、11对main.py和check_useless_excludes.py的编辑后步骤12直接submit，全程无测试或脚本执行", [7,11,12]),
        (8, I, "每次编辑后都重跑python -m sqllineage.core -e ... CLI探针（步骤6、9、12、15、18、21、24），验证以检测器不识别的形式反复执行", [5,6,9,12]),
        (9, V, "步骤35对tokens.py的_extract_string做了实质性重写，最后一次python reproduce.py验证在步骤34，该修改提交前未被验证", [34,35]),
        (10, I, "agent自建fixture（iface.py/foo.py）并在步骤5-6用mypy验证通过，之后仅剩重复搜索无更多编辑；验证以自建探针形式存在", [1,3,5,6]),
        (11, V, "步骤1编辑device.py后运行即结束（early_exit），全程无任何测试或脚本执行，编辑未获任何验证", [1]),
        (12, V, "新建close.py并修改main.py（步骤3-12）后步骤13直接submit，全程无测试或探针运行", [9,11,13]),
        (13, V, "步骤13对snippets.py的实质性编辑后，步骤14只是echo打印预设文案'Issue should be resolved'，并非真实执行Markdown处理，编辑从未被真实验证", [13,14,15]),
        (14, I, "步骤35在编辑status.py后运行./bin/dvc status --recursive CLI探针（返回unrecognized arguments，实际检验了修改）；且运行以early_exit结束并未submit，'提交且从未验证'的说法不成立", [34,35,40]),
        (15, V, "步骤17对plots.py的编辑是最后一步，运行即终止（early_exit），全程无任何测试执行，编辑未获验证", [17]),
        (16, V, "步骤5编辑entrypoint_parser.py后步骤6直接submit，全程无任何测试或脚本执行", [5,6]),
        (17, V, "新建exceptions.py并编辑telemetry/base.py（步骤6-7）后运行终止，全程无任何测试执行，修改未获验证", [6,7]),
        (18, V, "步骤5、7对client.py的编辑后步骤8直接submit，全程无测试或探针运行", [5,7,8]),
        (19, V, "步骤4编辑ops/model.py后步骤5直接submit，全程无任何测试执行", [4,5]),
        (20, V, "自建测试test_iso8601.py在步骤16-23每次都Traceback失败，步骤24-25又对atdate_format.py做了两处编辑后即submit，最终修改从未被复验", [23,24,25,26]),
        (21, V, "最后一次reproduce.py验证在步骤24（报错），步骤32对artifactory.py的URL拼接逻辑做了实质性重写后即submit，未再验证", [24,32]),
        (22, V, "步骤25、27对pipeline.py的编辑应用后步骤28直接submit，全程无测试或探针运行", [25,27,28]),
        (23, V, "唯一一次验证reproduce.py在步骤2（任何编辑之前），步骤22对env_settings.py应用了实质性的explode_env_vars重写后即submit，之后零验证", [2,22]),
        (24, I, "步骤14在编辑core.py后重跑sqllineage -f reproduce.sql CLI探针检验修改，验证以检测器不识别的CLI形式执行过", [13,14,16]),
        (25, V, "唯一验证在步骤2（编辑前），步骤9-35对mat_utils.py的大量实质性编辑（多次成功应用）后直接submit，从未重跑reproduce.py", [2,9,35]),
        (26, I, "agent自建test.spdx fixture并在步骤12（编辑tagvalue.py之后）重跑pyspdxtools_parser --file test.spdx探针，验证以自建fixture+CLI形式执行过", [2,11,12]),
        (27, V, "轨迹仅有搜索、阅读、一次编辑（step5）后直接submit（step6），全程无任何测试或脚本执行", [5,6]),
        (28, I, "检测器漏识别了验证：step17/20 datalad clone https://osf.io/q8xnk/、step21 datalad clone ///是对改动后decode_source_spec的真实CLI探针，step20观察到映射生效", [17,20,21]),
        (29, V, "step5-21全是编辑（多数被拒），step22直接submit，全程唯一shell是开头的ls -F，无任何测试", [21,22]),
        (30, V, "编辑client.py添加auth参数后（step9/11/12）直接submit（step13），所有shell步骤均为grep/ls搜索，无测试执行", [12,13]),
        (31, V, "编辑juju-crashdump脚本（step5-9，含多次被拒）后step10直接submit，无任何测试或脚本运行", [9,10]),
        (32, V, "step2运行reproduce_bug.py（NameError）后再未运行任何验证，step13对parse_edgelist的实质性修复（前面7-12均被拒、仅此次成功）从未被复测", [2,13]),
        (33, V, "编辑did_change_workspace_folders处理（step4-6）后step7直接submit，全程无shell测试步骤", [6,7]),
        (34, I, "agent自建fixture foo.toml并两次运行toml-sort foo.toml（step2复现报错、step5修复后通过），是检测器未识别的真实验证", [2,5]),
        (35, I, "agent创建reproduce.sql并在每次修复后运行sqlfluff lint reproduce.sql --dialect mysql（step2/3/7/10），step10通过，属自建fixture探针验证", [2,7,10]),
        (36, V, "step6-8三处编辑后step9直接submit，shell步骤仅为开头的ls，无任何测试", [8,9]),
        (37, V, "仅一步编辑（step2）后step3直接submit，全程无测试执行", [2,3]),
        (38, V, "step2/3编辑Data/Index定义后step4直接submit，轨迹中无任何命令执行", [3,4]),
        (39, V, "step4编辑BIDSReader后step5直接submit，无任何测试或脚本运行", [4,5]),
        (40, V, "step4编辑dump函数后step5直接submit，轨迹无任何shell/测试步骤", [4,5]),
        (41, V, "step6编辑order_by逻辑后step7直接submit，无任何测试执行", [6,7]),
        (42, V, "step2-8多次编辑municipalities过滤逻辑（部分被拒）后step9直接submit，全程无测试", [8,9]),
        (43, V, "最后的验证是step7的python -c dir()探测（非功能测试，reproduce.py只在step2跑过），step25对from_shapely/geom处理的成功实质编辑之后再无任何运行即submit", [7,25]),
        (44, V, "step2/3两处编辑HTTPResponse构造后step4直接submit，无任何测试", [3,4]),
        (45, V, "step4编辑后仅运行rm reproduce.py（step5，文件不存在报错），非测试；随后submit，无任何验证执行", [4,5,6]),
        (46, V, "step14-37共24次编辑尝试（几乎全部value_error被拒）后直接submit，全程无任何测试或脚本运行", [14,37]),
        (47, V, "step4/6两处编辑_get_default_credentials_path调用后step7直接submit，无测试", [6,7]),
        (48, V, "step3-5编辑构造函数后step6直接submit，轨迹中无任何测试执行", [5,6]),
        (49, V, "step13-31共19次编辑尝试（多为缩进错误被拒）后直接submit，无任何测试", [13,31]),
        (50, V, "step2编辑Call.create后step3直接submit，全程无测试", [2,3]),
        (51, V, "step2编辑_url_re后step3尝试python reproduce.py但file_not_found（脚本从未创建，测试实际未运行），随后submit；声称的『未执行任何测试/脚本』属实", [2,3,4]),
        (52, V, "step38的python reproduce_issue.py仍runtime_error，step39对fetch()签名的实质性修改（增加skip_sha256_check参数）之后再无任何运行即结束，最后这次代码改动未被验证", [38,39]),
    ]],
    # ---- 70B termination_anomaly (joined through old packet mapping) ----
    *[( "review_v7_70b", "termination_anomaly", n, verdict, r, s) for n, verdict, r, s in [
        (1, I, "no_submit报警但exit_status为'submitted (exit_context)'，且存在针对lexicon/providers/memset.py的非空补丁（913字符），说明运行已正常提交，检测器误读", []),
        (2, I, "exit_status为'submitted (exit_context)'，补丁含pyflakes/checker.py等非空修改（1849字符），已正常提交，no_submit为误读", [38]),
        (3, I, "exit_status为'submitted (exit_context)'，补丁含job_script.sh、reproduce.py及reproman源码修改，step67运行reproduce.py成功后提交，no_submit为误读", [67]),
        (4, I, "exit_status为'submitted (exit_context)'，补丁修改asv/commands/profiling.py（790字符非空diff），已提交，检测器误读", []),
        (5, I, "exit_status为'submitted (exit_context)'，补丁非空（122915字符），已提交，no_submit为误读", [18,19]),
        (6, I, "exit_status为'submitted (exit_context)'，补丁修改sqllineage/core.py（2005字符），step24还运行了验证命令，已正常提交，检测器误读", [24]),
        (7, V, "exit_status仅为'exit_context'（无submitted），补丁为空，step35仍在查看eppy的.idf资源文件，轨迹以探索状态中断且无提交动作，确属未提交结束", [32,33,34,35]),
        (8, V, "补丁仅含fix_field_names.py（新建且只有1个空行），step18-20显示agent只编辑并运行了临时脚本test_field_names.py后用rm删除，step21提交，库源码itemadapter未改动，patch_ignores_source成立", [18,19,20,21]),
        (9, V, "补丁仅新增reproduce.py（beniget的复现脚本，368字符），step36-39只是对beniget.py的goto/搜索阅读，无任何库源码编辑，最终补丁确实只触碰临时脚本，claim成立", [36,37,38,39]),
        (10, I, "exit_status为'submitted (exit_context)'，补丁非空（含reproduce.py等368字符），已正常提交，no_submit为检测器误读", []),
        (11, I, "exit_status为'submitted (exit_context)'，补丁含sqlglot/tokens.py等多处真实修改（4041字符），step34运行reproduce.py验证后提交，no_submit为误读", [32,33,34]),
        (12, V, "补丁仅含reproduce_bug.py（B2_Command_Line_Tool的复现脚本，1675字符），step2-5全部围绕该脚本的运行与修改，无库源码改动，patch_ignores_source成立", [2,3,4,5]),
        (13, I, "exit_status为'submitted (exit_context)'，补丁非空（reproduce_bug.py），运行已正常提交，no_submit为检测器误读", []),
        (14, V, "补丁仅新增foo.py和iface.py两个复现/测试用接口文件（660字符），step169-172仍在搜索mypy_zope:plugin未做库源码编辑，patch_ignores_source成立", [169,170,171,172]),
        (15, I, "exit_status为'submitted (exit_context)'，补丁非空（foo.py、iface.py），已正常提交，no_submit为误读", []),
        (16, V, "exit_status为'early_exit'，补丁为空，全程仅2步（step0读device.py、step1一次编辑）即中断，无提交动作，no_submit且非误报", [0,1]),
        (17, I, "exit_status为'submitted (exit_context)'，补丁含charmcraft/commands/store/store.py真实修改（1368字符），已正常提交，no_submit为误读", [58,59,60]),
        (18, V, "补丁仅含get_org_api.py（pydatajson下的临时脚本，957字符），step20-22显示agent运行该脚本并rm掉test_import.py、get_organization.py后提交，库源码未改，patch_ignores_source成立", [20,21,22,23]),
        (19, V, "exit_status为'early_exit'，补丁为空，step40是对dvc/cli.py的编辑命令且无observation返回，轨迹在编辑中途被切断且无提交，no_submit成立", [37,38,39,40]),
        (20, V, "exit_status为'early_exit'，补丁为空，step17是对dvc/dvc/commands/plots.py的最后一次编辑且无结果返回即中断，无提交动作，claim成立", [14,15,16,17]),
        (21, V, "exit_status为'early_exit'，补丁为空，step6-7连续对pubg_python/domain/telemetry/base.py的import区做编辑后中断，无提交，no_submit成立", [4,5,6,7]),
        (22, I, "exit_status为'submitted (exit_context)'，补丁含repobee_feedback/feedback.py真实修改（19292字符），已正常提交，no_submit为检测器误读", [104,105,106,107]),
        (23, I, "exit_status为'submitted (exit_context)'，补丁非空（删除benchmarks/bench_002/.sqlfluff等，1140字符），已正常提交，no_submit为误读", [5,6,7]),
        (24, V, "补丁仅新增reproduce.sql（SQL复现脚本，379字符），step63-66全是create.sqlfluff/edit.sqlfluff等失败或无效的shell命令，未改动sqlfluff库源码，patch_ignores_source成立", [63,64,65,66]),
        (25, I, "exit_status为'submitted (exit_context)'，补丁非空（reproduce.sql），已正常提交，no_submit为检测器误读", []),
        (26, I, "exit_status为'submitted (exit_context)'，补丁含sqlglot/dataframe/sql/dataframe.py真实修改（1197字符），已正常提交，no_submit为误读", [49,50,51]),
        (27, I, "exit_status为'submitted (exit_context)'，补丁含senza/spotinst/components/elastigroup.py真实修改（5720字符），step37还做了编辑后提交，no_submit为误读", [35,36,37]),
        (28, I, "exit_status为'submitted (exit_context)'，说明运行实际已正常提交，检测器误读了状态；no_submit不成立", [44]),
        (29, I, "exit_status为'submitted (exit_context)'，最终补丁包含对artifactory.py 861:867的真实修复（步骤32），运行已提交，no_submit不成立", [32]),
        (30, V, "最终补丁只包含新建的reproduce.py（复现脚本），步骤12-19仅编辑该脚本后即submit，没有任何库源码修改", [12,13,19]),
        (31, I, "补丁diff修改了pvlib/temperature.py（noct_sam/pvsyst_cell，步骤11、14-18），是真实库源码，不满足『只动测试/临时脚本』", [11,14,18]),
        (32, I, "exit_status为'submitted (exit_context)'，运行已提交，检测器误读状态", [18]),
        (33, V, "步骤15-22对pydantic/env_settings.py的编辑全部被拒绝（'Your proposed edit has introduced new syntax error(s)'），最终补丁只含reproduce.py，库源码没有任何改动", [15,16,22]),
        (34, I, "exit_status为'submitted (exit_context)'，运行已提交，no_submit不成立", [22]),
        (35, I, "exit_status为'submitted (exit_context)'，运行已提交，检测器误读", [35]),
        (36, V, "补丁只含reproduce.py；步骤16-21编辑的是自建test_token.py，步骤22还将其rm删除，flask_dance库源码未改动", [17,22,23]),
        (37, V, "补丁只有新建的reproduce.sql；步骤204-211全是在conda环境里对reproduce.sql跑lint的find/shell探索，未改任何sqlglot源码", [207,209,211]),
        (38, I, "exit_status为'submitted (exit_context)'，运行已提交", [211]),
        (39, I, "exit_status为'submitted (exit_context)'，且补丁含README.md与reproduce.py修改，运行已提交", [74]),
        (40, V, "步骤7-12对networkx/readwrite/edgelist.py parse_edgelist的编辑连续被拒（'introduced new syntax error(s)'），最终补丁只含reproduce_bug.py，库源码零改动", [7,8,13]),
        (41, I, "exit_status为'submitted (exit_context)'，运行已提交", [13]),
        (42, V, "补丁只有新建的reproduce.sh（内容'pyls --tcp --host 127.0.0.1 --port 7003'），步骤14-21只是scroll_down阅读，无任何源码修改", [14,15,21]),
        (43, I, "exit_status为'submitted (exit_context)'，运行已提交", [21]),
        (44, I, "exit_status为'submitted (exit_context)'，且补丁含ciprs_reader/parser/section/header.py的真实修复（步骤98），运行已提交", [98,103]),
        (45, V, "补丁只有新建的reproduce.md；步骤155-162反复grep '--8<--'寻找实现位置，未改任何源码", [155,159,162]),
        (46, I, "exit_status为'submitted (exit_context)'，运行已提交", [162]),
        (47, V, "步骤18-24对geopandas/_vectorized.py的编辑反复被拒（语法错误），最终补丁只含reproduce.py，库源码没有任何改动", [18,19,25]),
        (48, I, "exit_status为'submitted (exit_context)'，运行已提交", [25]),
        (49, I, "exit_status为'submitted (exit_context)'，运行已提交；补丁虽异常（gitlink 'D:dvcregression'）但提交确实发生了", [35]),
        (50, I, "exit_status为'submitted (exit_context)'，补丁含matchms/filtering/.../require_correct_ionmode.py修改，运行已提交", [37]),
        (51, V, "补丁只有自建的remove_test.py（内容os.remove(\"test.py\")），步骤14-21只是cd/读README/跑该脚本，beniget源码未改", [18,20,21]),
        (52, V, "exit_status仅为'exit_context'，model_patch为None（无diff），运行既未提交也没有产出补丁，no_submit成立", []),
        (53, V, "补丁唯一文件是src/psyclone/tests/test_files/dynamo0p3/10.9_operator_first.f90（测试数据文件，步骤6-8只在阅读它），无库源码改动即submit（步骤12）", [6,8,12]),
        (54, I, "exit_status为'submitted (exit_context)'，补丁含edk2toolext/environment/extdeptypes/web_dependency.py的真实修改，运行已提交", [39]),
    ]],
    # ---- 70B edit_error + lost_edit (packets unchanged by the fix) ----
    *[( "review_v7_70b", det, n, verdict, r, s) for det, lst in {
        "edit_error": [
            (1, V, "步骤2-6对memset.py连续5次编辑被拒（IndentationError/'(' never closed），其中步骤3-6是同一行142的重复失败重试，无任何实质进展，属真实的编辑工具搏斗；步骤7才落盘成功", [2,3,4,5,6]),
            (2, V, "步骤5-12对adapter.py连续8次被拒，在两版同样报F821 undefined name的编辑间反复交替（Iterable / is_scrapy_item），始终未修import问题，最终也未在adapter.py落盘", [5,6,7,8,9,10,11,12]),
            (3, V, "步骤1-3对reproduce.py连续3次编辑被拒，同样的E999 SyntaxError（多行内容被压平）未改变策略，步骤4才成功；虽是scratch脚本但确实是重复撞同一错误的浪费", [1,2,3,4]),
            (4, V, "步骤9-11对generate.py连续3次编辑被拒（SyntaxError/IndentationError），步骤9与10错误相同，步骤12才落盘；针对真实源码文件的编辑工具搏斗成立", [9,10,11,12]),
            (5, V, "步骤13-16对client_options.py连续4次被拒，且是同一编辑（116:116 from_dict）逐字重复4次，同样的F821 undefined name 'ClientOptions'，无任何改动；步骤18换行号区间后才成功", [13,14,15,16]),
            (6, V, "步骤4-6对gitignore_parser.py连续3次被拒，同一E999 IndentationError的同一份编辑内容原样重试3次，直到步骤7才以更大区间落盘", [4,5,6,7]),
            (7, V, "步骤4-10对temperature.py连续7次编辑被拒，错误在F821 eta_m_ref / invalid syntax / F821 noct_adj间反复，同一错误指纹多次重复无进展，步骤11才落盘", [4,5,6,7,8,9,10]),
            (8, V, "同一run的第二段搏斗：步骤14-17对temperature.py的pvsyst_cell连续4次被拒（cannot assign to literal / F821 eta_m反复出现），步骤15与17是同一编辑原样重试，步骤18才成功", [14,15,16,17,18]),
            (9, V, "步骤10-21对env_settings.py连续12次被拒，几乎全是同一E999 IndentationError下explode_env_vars编辑的行号微调重试，无实质进展，步骤22才落盘", [10,11,12,13,14,15,16,17,18,19]),
            (10, V, "步骤7-12对edgelist.py的parse_edgelist编辑连续6次被拒，action与错误（E999 IndentationError）完全相同，属零变化的机械重试，步骤13才落盘", [7,8,9,10,11,12,13]),
            (11, V, "步骤5,8-12对array.py连续6次被拒，6种不同写法全部报同一F821 undefined name 'Tuple'/'Any'（始终未加import），未吸取教训，属无效循环", [5,8,9,10,11,12]),
            (12, V, "步骤15-24对_vectorized.py连续10次被拒，步骤17-24是同一编辑（out=[] try/for/compat.USE_PYGEOS...）逐字重试8次，错误恒为F821 undefined name 'geoms'，步骤25才落盘", [15,16,17,18,19,20,21,22,23,24]),
            (13, V, "步骤13-22对config.py连续10次被拒，同一份带E111 indentation错误的XDG config_path编辑反复重试，行号从181微调到180也无济于事；补丁为空，最终也未落盘", [13,14,15,16,17,18,19,20,21,22]),
            (14, V, "同一run搏斗的延续段：步骤23-30对config.py又连续8次被拒，仍是同一E111编辑原样重试，直到步骤31才落盘，claim真实成立", [23,24,25,26,27,28,29,30,31]),
        ],
        "lost_edit": [
            (1, I, "parse_sas.py是本run步骤3新建的实验脚本（create parse_sas.py），import ply失败后步骤6已被rm删除，且它只是探索用scratch脚本而非修复，补丁交付的reproduce.sas是复现用例", [3,4,6]),
            (2, I, "get_organization.py是步骤0本run新建的草稿，import ckantools失败后被放弃；补丁中的get_org_api.py用requests重新实现了等价的get_organization函数，工作被重做而非丢失", [0,1,4]),
            (3, V, "env_settings.py是真实源码，步骤12次被拒后步骤22终于落盘explode_env_vars修复，但最终补丁只含新建的reproduce.py，修复成果被丢弃且无等价重做证据", [22]),
            (4, I, "test.spdx是步骤1本run新建的复现fixture（PackageVersion: 1:**:**2.36.1...），非源码修复；真正的修复在spdx/parsers/tagvalue.py且已在补丁中", [1,2,3]),
            (5, V, "edgelist.py是真实源码，步骤7-12被拒后步骤13落盘parse_edgelist修复，但最终补丁只含新建的reproduce_bug.py，修复丢失且未见重做", [13]),
            (6, V, "_vectorized.py是真实源码，步骤15-24被拒后步骤25落盘_shapely_to_pygeos/from_shapely的geoms处理修复，最终补丁却只含reproduce.py，修复被丢弃", [25]),
            (7, I, "test.py是步骤0本run新建的scratch脚本，用于验证lambda *args行为，步骤11已被rm删除，不属于源码修复；补丁内容（remove_test.py）也印证它只是临时文件", [0,1,9,11]),
        ],
    }.items() for n, verdict, r, s in lst],
    # ---- 70B submit_despite_failure + 405B (packets unchanged by the fix) ----
    *[( "review_v7_70b", "submit_despite_failure", n, verdict, r, s) for n, verdict, r, s in [
        (1, I, "step5最后一次验证失败是自写探针parse_sas.py报ModuleNotFoundError: No module named 'ply'，属环境依赖缺失，与任务正确性无关；step6删除探针后提交", [5,6,7]),
        (2, I, "真正的验证step23 test_parameterize2.py成功运行（Output: None，未再崩溃）；step26的失败只是重复执行rm已删文件报file_not_found，不是验证失败", [23,25,26,27]),
        (3, I, "step7 reproduce.py输出'Error message: boop'，修复验证成功且无报错，检测器把正常退出误标为generic_error，失败并不存在", [7,8,9]),
        (4, I, "step11 pytest 5个失败全部在test_libsim.py，与本次netcdf.py修改无关（test_netcdf全部通过），属既有无关失败测试", [11,12]),
        (5, V, "step23目标行为测试test_iso8601.py仍报UnexpectedCharacters无法解析'2022-07-25t14:30'，之后仅做语法编辑（step24/25）未再运行任何验证就于step26提交", [23,24,25,26]),
        (6, I, "step8失败是探针脚本import celery报ModuleNotFoundError，属环境缺依赖，并非修复正确性相关验证失败", [8,9,10]),
        (7, I, "step12失败是探针脚本自身import不存在的exp_refs_by_name（API版本不匹配），属探针/环境问题，不构成对修复的目标行为验证失败", [12,13]),
        (8, I, "step25验证实际通过（两行'Redirect middleware handled 308 correctly'）；step27失败只是对已删文件再次unlink报file_not_found，与任务无关", [25,27,28]),
        (9, I, "step9 pytest 7个失败集中在test_changelogs.py（InvalidVersion、ChunkedEncoding网络类错误），与finder.py的URL校验修改无关且test_finder全过，属无关既有失败", [9,10]),
        (10, I, "step20失败是清理脚本remove_test.py因test.py已被step11删除而报FileNotFoundError，属清理操作而非验证；此前step10复现脚本已成功运行", [10,20,21]),
    ]],
    *[( "review_v7_405b", det, n, verdict, r, s) for det, lst in {
        "edit_error": [(1, V, "step18-20（实际延续到35）对reproduce.py的同一编辑被连续拒绝，每次都是相同的E999 unterminated string literal，错误指纹不变且无任何进展", [18,19,20])],
        "termination_anomaly": [
            (1, V, "轨迹最后一步step36是一次编辑操作，全程无submit/finish动作即结束，证据与no_submit主张一致", [35,36]),
            (2, V, "model_patch只新增scratch脚本reproduce.py（62行测试脚本），没有任何pydantic库源码被修改，与patch_ignores_source判据吻合", [57,59,61,64]),
            (3, V, "轨迹止于step64的编辑动作，其后没有任何submit/finish步骤，运行在编辑中途结束", [62,63,64]),
            (4, V, "轨迹止于step106的编辑动作，无submit/finish；此前step99-105的探针仍在反复报type_error，运行即此中断", [104,105,106]),
        ],
        "verification_gap": [(1, V, "step2/6/8三次编辑tvcatchup.py后全程无测试套件或自写脚本执行，step3/7/9仅运行的streamlink CLI每次都因无关的rtbf插件加载错误失败（attr_error），修复从未被有效验证即于step10提交", [3,7,9,10])],
    }.items() for n, verdict, r, s in lst],
    # ---- main-sample new rules (review_v7_newrules) ----
    *[( "review_v7_newrules", "lost_edit", n, verdict, r, s) for n, verdict, r, s in [
        (1, I, "step63对html_renderer.py render_list_item的改动在step70被原样回退（新旧互换），净改动为零；补丁在mistletoe/block_tokenizer.py交付了针对同一问题（列表松散性/<p>）的修复", [63,70]),
        (2, V, "step51/53/61/62对真实源码non_parametric_ops.py（Barrier compute_matrix等）编辑成功，但补丁仅含reproduce_issue.py等新建文件（+185/-0），无任何源码改动，修复被整体丢弃且未在他处重做", [51,53,61,62]),
        (3, I, "step54在resource.py metadata_validate加MisplacedFieldsHint/MisplacedMissingValuesHint，step68已自行回退；补丁在errors/general.py定义了同名hint类并在resource/validate.py引用，等效修复已在其他文件交付", [54,68]),
        (4, V, "step73-87对dask/dataframe/core.py quantile逻辑的多轮实现编辑在step92被回退回原代码，补丁仅含TASKS.md和测试脚本（+1003/-0），无源码修复，分位数修复工作被丢弃", [73,77,83,85,87,92]),
        (5, I, "step68在_utils.py加frozen处理后step69即回退；补丁在typing/_implementations.py将DEFAULT_DATACLASS_OPTIONS设为frozen=False，同一问题已用更优方案交付", [68,69]),
        (6, V, "step49（Instruments.call避免list()）、step55-57（_run.py复用batch、就地排序减少循环垃圾）是对真实源码的实质性修复尝试，补丁只有debug_garbage.py等新文件（+494/-0），无任何源码改动", [49,51,55,56,57,87]),
        (7, I, "step42对TiffImagePlugin.py的libtiff tile改动在step55被回退为原代码；补丁在src/PIL/Image.py _getdecoder中交付了等价的TIFF/libtiff解码处理", [42,55]),
        (8, I, "step43/45/52只是在common.py/unpack.py插入DEBUG print，step56/57又将其删除，属无实质内容的调试性编辑", [43,45,52,56,57]),
        (9, V, "step35-96对pymzml/run.py _init_iter/iterparse事件等做了17次实质性修复编辑（如step92改events=(\"end\",)），补丁仅含check_iterator.py等新脚本（+669/-0），无源码改动", [35,37,92,96]),
        (10, V, "step70-78对sqlglot/generator.py json_path单引号转义（''）及postgres/snowflake方言的修复编辑成功落盘，补丁仅含debug_json_path.py等新文件（+196/-0），修复丢失", [70,72,74,76,78]),
        (11, I, "step23-43在rachioobject.py加UUIDEncoder/json处理，step52已整体回退到原始代码；补丁在rachiopy/notification.py中交付了json/uuid处理，等效方案已在补丁内", [23,24,43,52]),
        (12, I, "step83只是向errors.py中间件插入SERVER_ERROR_DEBUG print，step97即删除，纯调试性编辑无实质内容", [83,97]),
        (13, V, "step40-73对stwcs/wcsutil/headerlet.py的alternate WCS删除逻辑做了实质性修复编辑，补丁内容为二进制乱码（FITS数据），不含该文件任何改动，修复丢失", [40,47,60,61,67,70,73]),
        (14, I, "step29/30的x_dim/y_dim参数实验在step41被自行回退；补丁在rioxarray/raster_array.py交付了坐标对齐修复（CHANGES_SUMMARY.md说明rounding方案），属被更好方案取代的实验", [29,30,41,42]),
        (15, I, "step35在mapping.py setitems加auto_mkdir建父目录逻辑，step88已回退；补丁改为在fsspec/core.py url_to_fs中移除'auto_mkdir'传递，从根因修复同一问题", [35,88]),
        (16, V, "step60-97对sqlfluff rebreak.py indent_to别名换行逻辑的实质性修复编辑（step97又回退为原逻辑），补丁仅有.sqlfluff_simple和debug_issue.py等新文件，无任何源码改动，工作被丢弃", [60,68,69,77,80,81,92,93,97]),
        (17, I, "step60在typeinfer.py保留Omitted literal类型的尝试在step64自行回退；补丁在numba/core/types/misc.py增加literal_type属性并在typing/templates.py预处理参数，同一问题已用更好方案交付", [60,64]),
        (18, I, "被编辑的是conda site-packages下的wheel/wheelfile.py（非仓库源码文件），且step83/84只是删除agent自己加的DEBUG print", [83,84]),
        (19, I, "step35/40为防递归加_processing_strategy字段与import，step46/47已自行回退；补丁在pack.py/unpack.py中以field_ctx.metadata检查方式交付了字段级策略递归修复", [35,40,46,47]),
        (20, I, "step60给models.py _api附加query string的改动在step79自行回退；补丁改为在github3/repos/contents.py新增保留ref的refresh()方法，同一问题已用更优方案交付", [60,79]),
        (21, I, "step31-34在xonsh/prompt/base.py过滤空片段的改动在step37回退为原始一行；补丁在xonsh/prompt/gitstatus.py重写update处理非git仓库场景，同一症状已定向修复", [31,32,34,37]),
        (22, I, "step66/67/72全部是插入DEBUG print，step69/70/91又逐一删除，纯调试性编辑无实质内容", [66,67,69,70,72,91]),
        (23, I, "step45的ark路由被step60回退、step62的404 errorhandler被step74回退；补丁在src/bioregistry/app/impl.py注册了相同的handle_ark_slash_redirect errorhandler，等效修复已交付", [45,60,62,65,74]),
        (24, V, "step86在fenced_leaf_block_processor.py让围栏代码块关闭BlockQuote（并配step89 import）是实质性修复尝试，step91/92又回退；补丁仅含debug_detailed.py等新文件（+293/-0），无源码修复交付", [86,89,91,92]),
    ]],
    *[( "review_v7_newrules", "submit_despite_failure", n, verdict, r, s) for n, verdict, r, s in [
        (1, V, "最后验证(步骤90) test_basic FAILED，断言'Electric Field/Ex' in ds失败——正是本次斜杠改名修复破坏的既有测试，agent在步骤91无视失败直接提交", [89,90,91]),
        (2, I, "步骤52全套单测失败后，agent先git stash(53)在未打补丁的原代码上复现同一失败(54, shapely GeometryTypeError)，证明是与auth修改无关的既有失败测试；步骤51相关test_auth测试全部通过(55 stash pop后提交)", [51,52,53,54,55,58]),
        (3, I, "最后失败(74)是自写smoke脚本自身缺陷：注册了PassthroughResponse却未发任何请求，触发'Not all requests have been executed'断言；此前步骤61(96 passed)、67(final_validation)、71(passthrough测试)均通过", [61,67,71,74,75]),
        (4, I, "步骤77的collection error原因是'ModuleNotFoundError: No module named spacy'（tests/client/test_dataset.py导入环境缺失依赖），与客户端record validation修复无关，属环境类既有错误", [76,77,80]),
        (5, I, "最后验证(77)是故意加`timeout 5`运行MNIST示例以观察进度条输出的诊断探针，exit 124是预期结果；步骤76 test_normal_case.py通过，非真实失败信号", [74,76,77,79]),
        (6, U, "最后验证(68) test_legend失败(IndexError size 0)，位于本次legend修改相关区域，但agent在步骤57-59自己怀疑是continuous colorbar为空/matplotlib行为的既有问题，计划revert对照却未完成基线验证，无法确认失败由补丁引起或与之无关", [45,57,58,59,68,69]),
        (7, I, "步骤41 exit 5仅因`-k \"infinite\"` 13 deselected、没有任何测试运行('no tests ran')，并非任务正确性失败信号，agent随后仅读取文件并提交", [41,44]),
        (8, I, "步骤64实际'598 passed, 6 skipped'，exit 1仅因对测试子集运行触发了fail-under=100的覆盖率门槛('Coverage failure: total of 22 is less than fail-under=100')，非正确性失败", [63,64,69]),
        (9, V, "最后验证(64) test_get_xy_steps失败'assert 4 == 3'，正是本要修复的hres网格步长函数，agent无视失败信号于步骤66提交", [63,64,66]),
        (10, V, "补丁前步骤7/19全套测试通过(exit 0)；补丁(步骤25加self.save())后test_send_email持续失败(Database access not allowed, RuntimeError)，步骤28/35/42/50均失败，agent未解决于步骤52提交", [7,25,28,42,50,52]),
        (11, V, "基线步骤6为'58 passed'；改为default main后步骤29出现6个失败，至最后验证(43)仍有test_load_partial_config失败('1 failed, 51 passed')，agent未修复该回归即于步骤46提交", [6,25,29,43,46]),
        (12, I, "步骤45 exit 4是pytest 'ERROR: not found: Tests/test_image.py::TestImage::test_save'（no tests ran），属测试选择错误而非任务正确性失败信号", [44,45,47]),
        (13, V, "最后验证(70) 4个测试失败(test_do_authn等, 'assert 5 == 4')，直接检验apply_binding新增status字段的输出契约，agent在thought中明知失败仍于步骤73提交", [69,70,73]),
        (14, I, "最后失败(67)是`pytest ... | grep -i \"both host and p\"`的grep无匹配返回1，属预期诊断探针；此前步骤52-65(含全套tests/运行)全部exit 0", [64,65,67,68]),
        (15, V, "补丁修改了scan_policies.py/folder.py的同步过滤逻辑后，test_invalid_permissions（.dot_file未被过滤出文件列表）在步骤64和最后验证(73)反复失败，agent未解决即于步骤74提交", [32,33,64,73,74]),
        (16, I, "步骤57为demo.py演示脚本，输出中的'Exit code: 1'是relint对故意坏文件的预期lint结果，命令实际以exit 0结束（观察记录末尾明确'completed with exit code 0'）；步骤52单测全过", [52,57,59]),
        (17, I, "最后验证(62)实际'11 passed'全部通过，exit 1仅因coverage fail-under报错（子集运行的覆盖率门槛），非任务正确性失败；步骤61 final_verification也通过", [61,62,66]),
        (18, I, "最后验证(77) test_lightgbm::test_expgrad_classification因pandas FutureWarning/ChainedAssignmentError失败，属第三方库版本/环境问题，与input validation修复无关；步骤76 final_verification.py通过", [76,77,78]),
        (19, V, "最后验证(89) tests/describe 6 failed，agent在步骤89的thought中自己承认'I know that my change breaks some existing CSV tests'，仍于步骤92提交", [88,89,90,92]),
        (20, I, "最后验证(59) final_verification.py实际以exit 0结束且所有场景显示'✅ CORRECT'；检测器把演示输出文本中的'Exit Code: 2 (FAILURE)'误读为命令退出码，最后一次验证并未失败", [58,59,62]),
        (21, I, "最后验证(69)失败的3个用例均为test_run[...attach_pid]，错误是'Timed out waiting for debug server to connect'的环境类超时，与noDebug子进程修复无关；步骤63(test_nodebug相关9 passed)、64(7 passed)针对性验证均通过", [63,64,69,71]),
    ]],
]


def main() -> int:
    total = 0
    for review_dir in sorted({d for d, *_ in DECISIONS}):
        dir_path = ROOT / review_dir
        old_term = {}
        term_map_path = dir_path / "old_termination_mapping.json"
        if term_map_path.exists():
            old_term = json.loads(term_map_path.read_text(encoding="utf-8"))

        # index current packets: (detector, packet_no) and (instance, pattern)
        packets: dict[tuple[str, ...], Path] = {}
        for p in sorted(dir_path.glob("*_*.json")):
            if p.name.startswith("run_") or "mapping" in p.name:
                continue
            det, _, no = p.stem.rpartition("_")
            d = json.loads(p.read_text(encoding="utf-8"))
            packets[(det, no)] = p
            if det == "termination_anomaly":
                pattern = d["finding"]["detail"]["pattern"]
                packets[("termination_anomaly", f"{d['instance_id']}|{pattern}")] = p

        rows: list[dict] = []
        seen_ids: set[str] = set()
        for rdir, det, no, verdict, reason, steps in DECISIONS:
            if rdir != review_dir:
                continue
            key: tuple[str, ...]
            if det == "termination_anomaly":
                old = old_term.get(f"termination_anomaly_{no:03d}")
                key = None
                if old is not None:
                    key = ("termination_anomaly", f"{old['instance_id']}|{old['pattern']}")
            else:
                key = (det, f"{no:03d}")
            path = packets.get(key) if key else None
            if path is None:
                # the finding this verdict judged no longer exists (suppressed
                # by the no_submit exit_status fix): recorded as stale, not a
                # label row
                continue
            packet = json.loads(path.read_text(encoding="utf-8"))
            fid = packet["finding_id"]
            assert fid not in seen_ids, f"duplicate verdict for {fid}"
            seen_ids.add(fid)
            rows.append(
                {k: packet[k] for k in (
                    "finding_id", "run_id", "finding_signature",
                    "identity_version", "instance_id", "raw_record", "code_sha256",
                )}
                | {
                    "detector": det,
                    "pattern": packet["finding"]["detail"]["pattern"],
                    "verdict": verdict,
                    "reason": reason,
                    "evidence_steps": steps or [packet["finding"]["start"]],
                    "evidence_reference": path.name,
                    "reviewer_type": "ai",
                    "review_method": "primary AI review of blinded evidence packet; not human",
                    "human_verified": False,
                }
            )

        # every surviving packet must have exactly one verdict; the newrules
        # dir also holds packets for detectors already judged in v6 (those
        # verdicts arrive via finalize_review --adopt), so scope the check
        # to the detectors actually reviewed this round
        reviewed_dets = {det for d2, det, *_ in DECISIONS if d2 == review_dir}
        covered = {r["finding_id"] for r in rows}
        live_ids = set()
        for (det, no), p in packets.items():
            if det == "termination_anomaly" and "|" in no:
                continue
            if det not in reviewed_dets:
                continue
            live_ids.add(json.loads(p.read_text(encoding="utf-8"))["finding_id"])
        missing = live_ids - covered
        extra = covered - live_ids
        assert not missing, f"{review_dir}: packets without verdict: {sorted(missing)[:5]}"
        assert not extra, f"{review_dir}: verdicts without packet: {sorted(extra)[:5]}"

        out = dir_path / "verdicts_v7.jsonl"
        out.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )
        total += len(rows)
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        print(f"{review_dir}: {len(rows)} verdicts {counts} -> {out}")
    print("total", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

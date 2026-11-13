# -*- coding: utf-8 -*-
"""青果选课表格解析回归。

样本取自真实响应（POST /hsjw/taglib/DataTable.jsp?tableId=2568，GBK），
只裁掉了无关的样式与脚本，单元格结构原样保留。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qk import kingo_xk as X  # noqa: E402

SAMPLE = """<script>try{parent.showTotalRecord('2568','2');}catch(e){}</script>
<table class="datalist">
<tr class='H'><td id='head_kc'>课程</td><td id='head_xf'>学分</td>
<td id='head_zxs'>总学时</td><td id='head_lb'>课程类别</td><td id='head_kcsx'>课程属性</td>
<td id='head_skbh'>上课班号</td><td id='head_skbjmc'>上课班级名称</td><td id='head_xkfs'>选课方式</td>
<td id='head_rkjs'>任课教师</td><td id='head_xk_status'>选课状态</td><td id='head_operation'>操作</td>
<td style='display:none;' id='head_kcdm'> </td><td style='display:none;' id='head_is_yxtj'> </td>
<td style='display:none;' id='head_skbjdm'>上课班级代码</td></tr>
<tr class='O' id='tr0' onclick='sglSelectedTr(this)'>
<td id='tr0_kc' name='kc' align='left'><a href='javascript:void(0)'
 onclick='parent.showCourseInfo("1500120001")'>[1500120001]体育1</a></td>
<td id='tr0_xf' name='xf' align='right'>1.0</td>
<td id='tr0_zxs' name='zxs' align='right'>32</td>
<td id='tr0_lb' name='lb' align='left'>通识课/必修课</td>
<td id='tr0_kcsx' name='kcsx' align='left'><font title='理论课/体育课'>理论课/体育课</font></td>
<td id='tr0_skbh' name='skbh' align='center'>101</td>
<td id='tr0_skbjmc' name='skbjmc' align='left'>气排球</td>
<td id='tr0_xkfs' name='xkfs' align='center'>学生网上选</td>
<td id='tr0_rkjs' name='rkjs' align='left'><a href="javascript:void(0);"
 onclick="parent.showTeacherInfo('2j7tq72ef0981lt4lw000048')">李红霞</a></td>
<td id='tr0_xk_status' name='xk_status' align='center'>选中</td>
<td id='tr0_operation' name='operation' align='center'>
<a href='javascript:void(0)' isview='1' onclick='parent.viewResume(this)'>查看</a> | 
<a href='javascript:void(0)' onclick='parent.cancelElectiveCourse(this)'>退选</a></td>
<td id='tr0_kcdm' name='kcdm' style='display:none;'>1500120001</td>
<td id='tr0_is_yxtj' name='is_yxtj' style='display:none;'>1</td>
<td id='tr0_skbjdm' name='skbjdm' style='display:none;'>1500120001-101</td></tr>
<tr class='E' id='tr1'>
<td id='tr1_kc' name='kc'><a href='javascript:void(0)'>[1500120002]体育2</a></td>
<td id='tr1_xf' name='xf'>1.0</td>
<td id='tr1_zxs' name='zxs'>32</td>
<td id='tr1_lb' name='lb'>通识课/必修课</td>
<td id='tr1_kcsx' name='kcsx'>理论课/体育课</td>
<td id='tr1_skbh' name='skbh'>102</td>
<td id='tr1_skbjmc' name='skbjmc'>篮球</td>
<td id='tr1_xkfs' name='xkfs'>学生网上选</td>
<td id='tr1_rkjs' name='rkjs'>王老师</td>
<td id='tr1_xk_status' name='xk_status'>未选</td>
<td id='tr1_operation' name='operation'></td>
<td id='tr1_kcdm' name='kcdm' style='display:none;'>1500120002</td>
<td id='tr1_is_yxtj' name='is_yxtj' style='display:none;'>0</td>
<td id='tr1_skbjdm' name='skbjdm' style='display:none;'>1500120002-102</td></tr>
</table>"""

EMPTY = """<script>try{parent.showTotalRecord('2548','0');}catch(e){}</script>
<table><tr class='H'><td id='head_kc'>课程</td><td id='head_rkjs'>任课教师</td></tr></table>"""


def main() -> int:
    checks = {}

    headers, rows = X.parse_table(SAMPLE)
    checks["表头字段数>=10"] = len(headers) >= 10
    checks["识别出2行数据"] = len(rows) == 2
    checks["总记录数=2"] = X.total_records(SAMPLE) == 2

    r0, r1 = rows[0], rows[1]
    checks["课程名剥前缀"] = X.course_name(r0) == "体育1"
    checks["课程代码"] = X.course_code(r0) == "1500120001"
    checks["上课班级代码"] = X.class_code(r0) == "1500120001-101"
    checks["教师无标签"] = r0.get("rkjs") == "李红霞"
    checks["班号/班级名"] = (r0.get("skbh"), r0.get("skbjmc")) == ("101", "气排球")
    checks["已选判定"] = X.is_selected(r0) is True
    checks["未选判定"] = X.is_selected(r1) is False

    # 提交载荷：kcdm,rkjs,tj,true  —— 与页面 saveDate() 一致
    checks["items 格式"] = X.build_items([r0]) == "1500120001,李红霞,1,true"
    checks["items 多行用|分隔"] = X.build_items(rows).count("|") == 1
    checks["tj=0 时按行内 is_yxtj"] = X.build_items([r1]) == "1500120002,王老师,0,true"
    checks["tj=1 强制允许调剂"] = X.build_items([r1], tj="1").endswith(",1,true")

    # 匹配
    checks["按名匹配"] = len(X.match(rows, name="体育1")) == 1
    checks["按教师匹配"] = X.match(rows, teacher="王")[0]["skbh"] == "102"
    checks["按代码匹配"] = X.match(rows, code="1500120002")[0]["skbjmc"] == "篮球"
    checks["无匹配返回空"] = X.match(rows, name="高等数学") == []
    checks["名含空格也能匹配"] = len(X.match(rows, name=" 体育 2 ")) == 1

    # 空表 / 退化
    checks["空表总记录=0"] = X.total_records(EMPTY) == 0
    _, erows = X.parse_table(EMPTY)
    checks["空表无数据行"] = erows == []
    checks["无 kcdm 时回退方括号"] = X.course_code({"kc": "[A001]某某课"}) == "A001"
    checks["无 skbjdm 时拼班号"] = X.class_code(
        {"kcdm": "A001", "skbh": "9"}) == "A001-9"

    ok = True
    for name, res in checks.items():
        ok &= bool(res)
        print(f"[{'PASS' if res else 'FAIL'}] {name}")
    print("\n" + (f"全部通过（{len(checks)} 项）" if ok else "有失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

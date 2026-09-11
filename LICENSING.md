# Licensing

Unless a file carries a different license notice, the original software in this
repository is licensed under the GNU Affero General Public License version 3
only (`AGPL-3.0-only`). The full license is in [LICENSE](LICENSE).
This includes the backend, frontend, evaluation code, experiment scripts, and
original code in notebooks. There is no separate MIT license for the frontend.

Third-party code and materials retain their own licenses. The repository's
default license does not replace those licenses or relicense third-party data.

- PM4Py is licensed under AGPLv3; CVXOPT under GPLv3 or later. Their notices
  and license terms remain applicable when distributing these dependencies.
- bpmn-js uses the [bpmn.io License](https://bpmn.io/license/). Its copyright
  and permission notice must be retained, and the bpmn.io watermark must remain
  visible and unobscured. The AGPL declaration covers our frontend code, not a
  relicensing of bpmn-js.
- Other dependencies retain the licenses supplied with their packages,
  including licenses for bundled components such as the CBC solver in PuLP.
- PMo-derived models and descriptions retain their CC BY 4.0 attribution and
  license requirements. See [the dataset attribution](data/eval/v1.0/ATTRIBUTION.md)
  and [the source record](data/pmo-dataset/SOURCE.md).

When distributing the application, retain applicable license notices and
provide the corresponding source as required by the licenses. A modified
AGPL-covered version used over a network must prominently offer its users
access to the corresponding source of that running version, as specified in
section 13 of the AGPL. A link to an unchanged upstream version does not cover
unpublished modifications.

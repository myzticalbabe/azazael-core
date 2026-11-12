# Sources for the emotional and female corpora

The two corpora in this directory keep digits out of their prose by rule: a figure
in a sermon is a claim without a command, and `tools/bible_check.py` refuses one
that has no repository path beside it. This file is where the figures live, with
the study that produced them and the limit that came attached. **A verse may be
quoted without this file; a number may not.**

Two conventions used below:

- **Tradition** means a body of work the corpus cites by name only. The corpus
  never says "studies show"; it says which tradition, and this register says what
  that tradition measured, on whom, and how large the effect was.
- **Limit** is not decoration. Where a finding is contested, replicated weakly, or
  measured on a narrow sample, the limit is written next to it, and the corpus
  verse is phrased so that it survives the limit.

---

## 1. Similarity, difference, and the size of the effect

**Hyde, J. S. (2005). The gender similarities hypothesis. _American Psychologist_.**
A metasynthesis of 46 meta-analyses (124 effect sizes). Most differences — the
commonly quoted figure is 78% — were small or close to zero. Hyde's claim is not
that no differences exist; it is that the default assumption should be similarity
and that a claimed difference carries the burden of proof.

- Used by: `female.json` → `same_weather 1:1`, `1:4`.
- Limit: a metasynthesis inherits the samples of the meta-analyses it summarises,
  which are disproportionately Western, educated, and young. "Most differences are
  small" is a claim about what has been measured, not a claim that nothing is ever
  different.

**Zell, E., Krizan, Z., & Teeter, S. R. (2015). Evaluating gender similarities and
differences using metasynthesis. _Psychological Bulletin_.**
Re-analysis confirming the broad pattern and adding that the average absolute
difference is small.

**Chaplin, T. M., & Aldao, A. (2013). Gender differences in emotion expression in
children: a meta-analytic review. _Psychological Bulletin_.**
555 effect sizes from 166 studies, 21,709 participants. Overall differences were
significant but **very small**: girls higher on positive and internalising
expression (g ≈ −0.08 to −0.10), boys higher on externalising expression
(g ≈ 0.09). Effects were moderated by age, interpersonal context, and task
valence — larger with unfamiliar adults, smaller with parents.

- Used by: `female.json` → `same_weather 1:2`.
- Limit: observational coding of children; the moderators matter more than the
  main effect, which is the point the verse makes.

**Hall, J. A., Gunnery, S. D., & Schlegel, K. (2025). Gender and accuracy in
decoding affect cues: a meta-analysis. _Journal of Intelligence_, 13(3), 38.
doi:10.3390/jintelligence13030038. PMID 40137070.**
1,188 effect sizes from 1,011 studies, total N = 837,637. Girls and women score
higher on decoding accuracy, and the difference is **small but extremely
consistent**: r = 0.12, d = 0.24. Evidence of publication bias was weak, and of
the moderators examined only the health status of the sample produced subgroups
without a significant difference. The authors also report that a higher
proportion of male authors was associated with a smaller reported difference — a
design and reporting effect worth naming whenever the finding is quoted as a fact
about nature.

- Limit: decoding accuracy on laboratory tasks is not empathy in a relationship,
  and a small mean difference with this many studies says nothing about a given
  pair. The citation was previously unfindable because it named no journal
  volume; it is now the full record above, and the figures are the abstract's.

- Limit: decoding accuracy on laboratory tasks is not empathy in a relationship,
  and a small mean difference says nothing about a given pair.

**Mendia, J., et al. (2024). Gender differences in alexithymia: insights from an
updated meta-analysis. _Personality and Individual Differences_.**
120 studies; pooled effect d = 0.22, men higher on alexithymia, with similar small
effects on the difficulty-identifying and difficulty-describing dimensions.

- Used by: `emotional.json` → `granularity 1:4`.
- Limit: self-report instruments; a small mean difference with large overlap.

**Barrett, L. F., & Bliss-Moreau, E. (2009). She's emotional. He's having a bad
day: attributional explanations for emotion stereotypes. _Emotion_.**
The belief that women are the more emotional sex is sustained less by observed
behaviour than by the explanations offered for identical behaviour: dispositional
for women, situational for men. Women are judged emotional for expressing *any*
emotion, anger included.

- Used by: `female.json` → `same_weather 2:1`, `2:2`.
- Limit: laboratory attribution paradigms; the effect is about judgement, not about
  the underlying state.

---

## 2. Granularity and the naming of states

**Smidt, K. E., & Suvak, M. K. (2015). A brief, but nuanced, review of emotional
granularity and emotion differentiation research. _Current Opinion in Psychology_.**
Establishes the construct and its validity: granularity is the precision with which
affective experience is differentiated, it is measurable, and it is associated with
better regulation.

**Hoemann, K., Lee, Y., Kuppens, P., Gendron, M., & Boyd, R. L. (2023). Emotional
granularity is associated with daily experiential diversity. _Affective Science_,
4, 291–306.** Granularity tracks with the diversity of what a person actually
experiences day to day, so it behaves like a measure of the situation as much as
of the person; it is not a fixed trait.

- Used by: `emotional.json` → `granularity 1:2`, `2:2`; `female.json` → `many_names 1:3`.
- Limit: mostly self-report and experience sampling; causation is not established by
  correlation, so the corpus says "can be acted on", not "cures".

---

## 3. Display, regulation, and labour

**Gross, J. J. (1998; 2015 process model).** Reappraisal alters the appraisal and
therefore the feeling; suppression alters expressive behaviour while the feeling
continues. The dissociation is the basis of `emotional.json` → `appraisal 2:3`
and `display 1:1`.

**Hochschild, A. R. (1983). _The Managed Heart_.** Emotional labour: the management
of feeling to produce a required state in another person; surface acting versus deep
acting. Named in service work, extended to the household in _The Second Shift_.

**Jeung, D.-Y., et al. (2018). Emotional labor and burnout: a review of the
literature.** Surface acting is consistently associated with emotional exhaustion
and diminished well-being; deep acting is generally less costly but not free.

- Used by: `female.json` → `unpaid_shift 1:2`.
- Limit: occupational samples, cross-sectional designs; the direction of causation
  between surface acting and exhaustion is not fully settled.

**Jack, D. C. (1991). _Silencing the Self: Women and Depression_.** Self-silencing:
the relational strategy of suppressing one's own needs and voice to preserve a
relationship, associated with depression. The construct is relational by design.
A 2026 _Sex Roles_ special section ("Advancing the next generation of research on
self-silencing") reviews three decades of cross-cultural work.

- Used by: `female.json` → `held_tongue 1:2`.
- Limit: the theory was built to explain a vulnerability, not to type a personality.
  Later work finds the pattern in men too, in different relational contexts. The
  corpus verse therefore names the habit and its cost, not a female trait.

**Rose, A. J. (2002). Co-rumination in the friendships of girls and boys. _Child
Development_.** Co-rumination — extensive discussion of problems — predicts closer
friendships *and* higher internalising symptoms, more strongly for girls.

**Nolen-Hoeksema, S. (1987; response styles theory).** Rumination as a response
style that prolongs distress and predicts depression.

**Johnson, D. P., & Whisman, M. A. (2013). Gender differences in rumination: a
meta-analysis. _Personality and Individual Differences_.** 59 studies, 14,321
participants: women higher on rumination (d = 0.24), brooding (d = 0.19), reflection
(d = 0.17). The authors describe the effects as statistically significant but small.

- Used by: `emotional.json` → `chronometry 2:2`; `female.json` context.
- Limit: self-report; small effects; rumination is a response, not a gender.

---

## 4. Backlash, the double bind, and anger

**Rudman, L. A., & Phelan, J. E. (2008); Rudman, Moss-Racusin, Phelan & Nauts
(2012).** Backlash: social and economic penalties for counter-stereotypical
behaviour. Agentic women risk being judged less likable; the "competence penalty"
and the "likability penalty" pull in opposite directions.

**Williams, M. J., & Tiedens, L. Z. (2016). The subtle suspension of backlash: a
meta-analysis of penalties for women's implicit and explicit dominance behavior.
_Psychological Bulletin_.** k = 63; women are penalised more than men for dominance,
**but the overall effect is small** (d ≈ −0.19). The authors emphasise moderation:
the penalty is not uniform across contexts.

- Used by: `female.json` → `narrow_door 1:1`, `1:2`, `1:3`.
- Limit: this is the most over-quoted literature in the corpus. A small average
  effect does not license "women cannot be assertive"; it licenses naming a bind
  that some women meet in some rooms. The verse `narrow_door 2:3` exists to keep
  the corpus honest about exactly this.

**Marshburn, C. K., et al. (2020). Workplace anger costs women irrespective of
race.** Anger expression costs women in competence and status judgements; the
pattern held across racial groups in the sample.

- Limit: vignette and laboratory designs; the size of the cost varies by industry
  and status.

**Brescoll, V. L. (2016). Leading with their hearts? How gender stereotypes of
emotion lead to biased evaluations of female leaders.** Reviews the mechanism by
which the emotion stereotype becomes a leadership penalty.

---

## 5. Ambivalent sexism

**Glick, P., & Fiske, S. T. (1996; 1997; 2011). Ambivalent sexism theory.** Hostile
sexism punishes; benevolent sexism rewards conformity to traditional roles. Both
sustain inequality; benevolent sexism is the harder one to see because it arrives
as care.

**Bareket, O., & Fiske, S. T. (2023). A systematic review of the ambivalent sexism
literature.** Benevolent sexism is associated with reduced performance, self-doubt,
and acceptance of restriction, and it is not experienced as hostility.

- Used by: `female.json` → `narrow_door 1:1` (context), `refusals 1:2`.
- Limit: correlational and experimental work; cultural variation is substantial.

---

## 6. Credibility, gaslighting, and pain

**Fricker, M. (2007). _Epistemic Injustice: Power and the Ethics of Knowing_.**
Testimonial injustice: credibility deflated because of prejudice about the speaker.
Hermeneutical injustice: a gap in the shared vocabulary that makes an experience
hard to name — which is also an argument for the granularity book.

**Kidd, I. J., & Carel, H. (2016). Epistemic injustice and illness.**
The medical case: the patient's account is discounted, and the discount is part of
the illness experience.

**Sweet, P. L. (2019). The sociology of gaslighting.** Gaslighting as a practice
that manufactures doubt about a person's own account; not a metaphor for
disagreement.

**Hoffmann, D. E., & Tarzian, A. J. (2001). The girl who cried pain: a bias against
women in the treatment of pain. _Journal of Law, Medicine & Ethics_.** Review of
evidence that women's pain is more often undertreated, and more often met with
sedation rather than analgesia.

- Used by: `female.json` → `believed 1:1`, `1:2`, `2:1`, `2:2`.
- Limit: the review is old and the literature is heterogeneous; the direction of the
  bias is well supported, the exact magnitude is not. The corpus therefore states
  the pattern and not a number.

---

## 7. Violence and coercive control

**CDC, _About Intimate Partner Violence_ (cdc.gov/intimate-partner-violence/
about/index.html, read 2026-11-02), drawing on the National Intimate Partner and
Sexual Violence Survey (NISVS).** The page states the two measures separately,
and they must be quoted separately:

- lifetime contact sexual violence, physical violence, or stalking by an intimate
  partner **with at least one related impact**: nearly **1 in 3 women** and about
  **1 in 8 men**;
- the same acts **without** the impact condition: **1 in 3 women** and **1 in 6
  men**.

Rape: about **1 in 4 women** and **1 in 26 men**.

This entry replaces an earlier one that gave "roughly 30% of women and 17% of
men" as a single measure — the women's figure from the impact condition, the
men's from the unconditional one, inflating the male side by a third. It also
drops a state-level range (21–48%) that had been attributed to a "2023/2024 data
brief" no one could name and that could not be re-derived from the page; a figure
the register cannot re-derive is a figure the register may not print.

- Used by: `female.json` → `locked_door 1:1`.
- Limit: acts alone are closer to symmetric than impact; fear, injury, stalking, and
  coercive control are not. The corpus verse says the distribution is uneven and
  that it is not a prediction about an individual — `locked_door 1:4` and
  `refusals 1:4` exist to prevent the statistic being used as a verdict.

**Stark, E. (2007). _Coercive Control_.** Violence as a pattern of domination
rather than a sum of incidents: monitoring, isolation, rules, micro-regulation.

- Used by: `locked_door 1:2`, `1:3`.

---

## 8. Minority stress and intersectionality

**Meyer, I. H. (2003). Prejudice, social stress, and mental health in lesbian, gay,
and bisexual populations.** Minority stress: excess stress from a hostile
environment, not from identity itself.

**Frost, D. M., & Meyer, I. H. (2023). Minority stress theory: application,
critique, and continued relevance.** Reviews the model, its extensions, and its
limits (measurement, within-group heterogeneity).

**Crenshaw, K. (1989; 1991).** Intersectionality: axes of identity configure
experience together; the sum-of-penalties model misdescribes both the harm and the
person.

- Used by: `female.json` → `many_women 1:1`, `1:3`, `2:3`.
- Limit: intersectionality is an analytic stance, not a scoring rubric. The corpus
  refuses to compute a total.

---

## 9. What the corpus deliberately does not cite

- **Stereotype threat** (Steele & Aronson, 1995) is not used as a load-bearing claim.
  The meta-analytic picture is contested and the effect is sensitive to
  operationalisation. A corpus that leans on it would be borrowing authority the
  literature has not settled.
- **Brain-imaging differences** are not used at all. A group-level activation
  difference is not a mechanism, and it is the most common way a popular account
  smuggles a stereotype back in.
- **Popular "women are from..." literature** is excluded by the same rule that
  excludes a number without a command.

---

## 10. How to extend this register

1. Find the primary paper, not the press release.
2. Record the effect size, the sample, and the design.
3. Write the limit in the same paragraph as the finding, before you are tempted to
   forget it.
4. Add the verse to the corpus **without a digit**. If the verse needs the number to
   make sense, the verse is not yet a verse.
5. Run the validator:

```bash
python tools/bible_check.py --check --corpus docs/bible/emotional/female.json
python tools/bible_check.py --check --corpus docs/bible/emotional/emotional.json
```

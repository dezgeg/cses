from django.http import HttpResponse
from django.shortcuts import render, redirect
from django import forms
from django.contrib import auth
from django.core.urlresolvers import reverse
from django.core.files import File
from django.core.files.base import ContentFile
from django.contrib.auth.forms import UserCreationForm
from django.db.models import Q
from django.forms import widgets

from utils import *
from django.conf import settings
import judging
import result
from datetime import datetime, timedelta
import models

from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import guess_lexer_for_filename, TextLexer

import re

import logging
logger = logging.getLogger(__name__)

class LoginForm(forms.Form):
	username = forms.CharField()
	password = forms.CharField(widget=forms.PasswordInput)

def login(request):
	if request.method == 'POST':
		form = LoginForm(request.POST)
		if form.is_valid():
			username = form.cleaned_data['username']
			password = form.cleaned_data['password']
			user = auth.authenticate(username=username, password=password)
			if user is not None and user.is_active:
				auth.login(request, user)
				return redirect('cses-index')
	else:
		form = LoginForm()
	return render(request, "login.html", {'form': form})

def logout(request):
	if request.method == 'GET':
		auth.logout(request)

	return redirect('cses-login')

@require_login
def index(request):
	contests = getUserContests(request.user)
	if len(contests) == 1:
		return redirect('contest/' + str(contests[0].id) + '/')
	else:
		return render(request, "index.html", {'contests': contests})


class ContestSubmitForm(forms.Form):
	task = forms.ModelChoiceField(queryset=Task.objects.none(), required=True)
	file = forms.FileField()
	language = forms.ModelChoiceField(queryset=Language.objects.all(), required=True)

	def __init__(self, contest, *args, **kwargs):
		super(ContestSubmitForm, self).__init__(*args, **kwargs)
		self.fields['task'].queryset = contest.tasks.all().order_by('contesttask__order')

@contest_page
def contest(request, contest):
	now = datetime.now()
	if now > contest.endTime or now < contest.startTime:
		message = 'Contest has ended.' if now>contest.endTime else 'Contest has not started.'
		return render(request, "contestend.html", {'contest':contest, 'message':message})
	if request.method == 'POST':
		form = ContestSubmitForm(contest, request.POST, request.FILES)
		if(form.is_valid() and form.cleaned_data['file']._size <= settings.CSES_MAX_SUBMISSION_SIZE):
			logger.info('adding submission at time %s',datetime.now())
			# TODO: error reporting
			submission = Submission(
				task=form.cleaned_data['task'],
				source=form.cleaned_data['file'],
				language=form.cleaned_data['language'],
				contest=contest,
				user=request.user,
				judgeResult=result.Result.PENDING,
				time=datetime.now()
			)
			submission.save()
			judging.master.addSubmission(submission)
		return redirect('/submissions/' + str(contest.id) + '/')
	else:
		form = ContestSubmitForm(contest)
		return render(request, "contest.html", {'contest': contest, 'form': form})

@contest_page
def submissions(request, contest):
	userSubs = Submission.objects.filter(user=request.user, contest=contest).order_by('-time')
	return render(request, 'submissions.html', {'submissions': userSubs, 'contest': contest})

def submissionCell(submitData, contestType, showLinks):
	if submitData == None:
		return '<td></td>'
	(submission, _, count) = submitData
	time = submission.submitTime()
	res = submission.judgeResult
	content = ''
	if contestType==models.Contest.Type.ICPC:
		content = str(count)+'<br/>'+str(time)
	else:
		content = str(submission.points())+'<br/>'+str(time)
	if showLinks:
		url = reverse('cses-viewSubmission', args=(submission.id,))
		content = '<a href="%s">%s</a>' % (url, content)

	return '<td class="%s" width="%d" height="%d">%s</td>' % (submission.colorType(), 40, 40, content)

def countResult(user, scores, contest):
	resTime = 0
	resPoints = 0
	for i in scores:
		if i == None:
			continue
		(submission, time, count) = i
#		res = submission.judgeResult
		score = submission.points()
		resPoints += score
		if score!=0:
			resTime += time
			resTime += submission.submitTime()
	return (-resPoints, resTime, user)

def userName(user):
	if user.first_name!='':
		return user.first_name
	return unicode(user)

def makeScoreboard(contest, showLinks, user):
	if user.is_superuser:
		showLinks = True
	submits = contest.latestSubmits()
	isIOI = contest.contestType==models.Contest.Type.IOI
	userM = None
	if showLinks or not isIOI:
		userM = User.objects.filter(Q(groups__in=contest.groups.all()) | Q(contest=contest)).distinct()
	else:
		userM = User.objects.filter((Q(groups__in=contest.groups.all()) | Q(contest=contest)) & Q(id=user.id)).distinct()
	if not user.is_superuser:
		userM = filter(lambda u: not u.is_superuser , userM)
	users = map(unicode, userM)
	userNames = map(userName, userM)
	taskM = contest.tasks.all().order_by('contesttask__order')
	tasks = map(unicode, taskM)
	table = [[(submits[t][u] if t in submits and u in submits[t] else None) for t in tasks] for u in users]
	uresults = sorted([countResult(i, table[i], contest) for i in xrange(len(users))])

	res = '<table border class="list">'
	res += '<thead><tr><th width=50>Rank</th><th width=100>Team</th><th width=50>Score</th><th width=50>Time</th>'
#	for task in tasks:
#		res += '<td>'+task+'</td>'
	for i in xrange(len(tasks)):
#		data = chr(ord('A')+i)
		data = tasks[i]
		if isIOI:
			data+='&nbsp;'+str(taskM[i].score)
		res += '<th width=50>'+data+'</th>'
	res += '</tr></thead>'
	for i in xrange(len(table)):
		(score, time, uidx) = uresults[i]
		res += '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>' % (str(i+1), userName(userM[uidx]), str(-score), str(time))
		row = table[uidx]
		res += ''.join([submissionCell(s, contest.contestType, showLinks) for s in row])
		res += '</tr>'
	res += '</table>'
	return res

#@contest_page
#def scoreboard(request, contest):
def scoreboard(request, cid):
	contests = Contest.objects.filter(id=cid)
	if len(contests) == 0:
		return redirect('cses-index')
	contest = contests[0]
	now = datetime.now()
	dt = contest.endTime-now if now<contest.endTime else timedelta(seconds=0)
	return render(request, 'scoreboard.html', {'contest': contest, 'scoreboard': makeScoreboard(contest, now>contest.endTime, request.user), 'time': now, 'remainingSeconds': dt.seconds+24*3600*dt.days})

def highlightedCode(submission):
	data = submission.source.read()
	formatter = HtmlFormatter(linenos=True, noclasses=True)
	try:
		lexer = guess_lexer_for_filename(submission.source.path, data)
	except:
		lexer = TextLexer()
	return highlight(data, lexer, formatter)

class CommentForm(forms.Form):
	comment = forms.CharField(widget=widgets.Textarea)

#@require_login
def viewSubmission(request, subid):
	subs = models.Submission.objects.filter(id=subid)
	if not subs:
		return redirect('cses-index')
	submission = subs[0]
	contest = submission.contest
	if datetime.now() <= contest.endTime and not request.user.is_superuser and submission.user!=request.user:
		return redirect('cses-index')
	code = highlightedCode(submission)
	if request.method=='POST':
		form = CommentForm(request.POST)
		if form.is_valid():
			comment = models.Comment(
					submission=submission,
					user=request.user,
					text=form.cleaned_data['comment'],
					line=-1,
					time=datetime.now())
			comment.save()
	comments = models.Comment.objects.filter(submission=submission).order_by('time')
	form = CommentForm()
	submissions = models.Submission.objects.filter(task=submission.task, user=submission.user, contest=submission.contest).order_by('-time')
	return render(request, 'viewsubmission.html', {'submission': submission, 'contest':contest, 'code':code, 'comments': comments, 'commentform': form, 'submissions': submissions})


class ImportForm(forms.Form):
	name = forms.CharField()
	data = forms.FileField()

from zipfile import ZipFile
def infilename(infile):
	parts = infile.split('.')
	if len(parts)<2:
		return None
	for i in xrange(1,len(parts)):
		if parts[i]=='out' or parts[i]=='ans':
			parts[i] = 'in'
			return '.'.join(parts)
	return None

def importArchive(data, contest):
	z = ZipFile(data, 'r')
	tasks = list(set([i.split('/')[0] for i in z.namelist()]))
	convert = lambda text: int(text) if text.isdigit() else text.lower()
	alphanum_key = lambda key: [ convert(c) for c in re.split('([0-9]+)', key) ]
	tasks.sort(key = alphanum_key)
	evaluator = File(open('../judge/run/compare.sh','r'))
	taskModels = {}
	for i in xrange(len(tasks)):
		t = tasks[i]
		task = models.Task(
				name=contest.name+"_"+t,
				evaluator=evaluator,
				timeLimit=1,
				score=100)
		task.save()
		ct = ContestTask(contest=contest, task=task, order=i)
		ct.save()
		taskModels[t] = task

	logger.debug('%s', tasks)
	nameset = set(z.namelist())
	for out in sorted(z.namelist(), key = alphanum_key):
#		out = outfilename(i)
		i = infilename(out)
		if i == None:
			continue
		if i not in nameset:
			logger.warning('No output-pair for input file %s',i)
			continue
		task = i.split('/')[0]
		logger.info('found input-output pair %s %s',i,out)
		case = models.TestCase(task=taskModels[task])
		case.input.save(i, ContentFile(z.read(i)))
		case.output.save(out, ContentFile(z.read(out)))
		case.save()

@require_login
def taskImport(request):
	if not request.user.is_superuser:
		return redirect('cses-index')
	if request.method == 'POST':
		form = ImportForm(request.POST, request.FILES)
		if form.is_valid():
			contest = models.Contest(
					name=form.cleaned_data['name'],
					active=True,
					startTime=datetime.now(),
					endTime=datetime.now(),
					contestType = models.Contest.Type.IOI)
			contest.save()
			taskfile = form.cleaned_data['data']
			importArchive(taskfile.file, contest)
	else:
		form = ImportForm()
	return render(request, 'import.html', {'form':form})

class UserCreateForm(UserCreationForm):
	team_name = forms.CharField()
	class Meta:
		model = User
		fields = ("username", "team_name")

def register(request):
	if request.method == 'POST':
		form = UserCreateForm(request.POST)
		if form.is_valid():
			new_user = form.save()
			# TODO: is there a way to do this without an extra transaction?
			new_user.first_name = form.cleaned_data['team_name']
			new_user.save()
			return redirect('cses-index')
	else:
		form = UserCreateForm()
	return render(request, "register.html", {'form':form})

class RejudgeForm(forms.Form):
	contest = forms.ModelChoiceField(queryset=Contest.objects.all(), required=True)

@require_login
def rejudge(request):
	if not request.user.is_superuser:
		return redirect('cses-index')
	if request.method == 'POST':
		form = RejudgeForm(request.POST)
		if form.is_valid():
			contest = form.cleaned_data['contest']
			submits = contest.latestSubmits()
			for task in submits:
				for user in submits[task]:
					(submission,_,_) = submits[task][user]
					logger.info('rejudging submit: %s %s',submission.user, submission.task)
					judging.master.addSubmission(submission)
	form = RejudgeForm()
	return render(request, 'rejudge.html', {'form':form})
